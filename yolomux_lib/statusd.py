"""Shared owner for public session and agent-status snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .app import TmuxWebtermApp
from .tmux.sessions import discover_status_sessions
from .tmux.tmux_utils import list_tmux_session_names
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import apply_service_process_priority
from .local_services.runtime import LocalRpcServiceState
from .local_services.runtime import reap_dead_client_leases
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service
from .statusd_protocol import STATUSD_PROTOCOL_VERSION
from .statusd_protocol import STATUSD_CODE_REVISION
from .statusd_protocol import STATUSD_SERVICE_NAME
from .statusd_protocol import StatusProtocolError
from .statusd_protocol import StatusSnapshotMetadata
from .statusd_protocol import validate_request
from .statusd_client import STATUSD_DEFAULT_IDLE_SECONDS
from .statusd_client import default_socket_path


STATUSD_MAX_SESSIONS = 256
STATUSD_CONCURRENT_HANDLER_LIMIT = 8

# Working/idle classification changes on every agent turn transition, and nothing about that
# transition (no approval prompt, no attention-ack) triggers an explicit invalidate() call. Without
# a bounded max age, a snapshot built while an agent was busy would be served forever, leaving tab
# status dots stuck on "running" long after the agent actually stopped. Mirrors the pre-statusd
# AUTO_APPROVE_CACHE_MAX_AGE_SECONDS safety net removed when this daemon replaced that cache.
STATUSD_SNAPSHOT_MAX_AGE_SECONDS = 5.003


class PersistentStatusService(LocalRpcServiceState):
    """One per-state-directory status owner with retained immutable bytes."""

    def __init__(self, socket_path: Path, idle_seconds: float = STATUSD_DEFAULT_IDLE_SECONDS):
        super().__init__(socket_path, prefix="yolomux-statusd", idle_seconds=idle_seconds)
        self.lock = threading.Condition(threading.RLock())
        self.build_lock = threading.Lock()
        self.app: TmuxWebtermApp | None = None
        self.session_names: tuple[str, ...] = ()
        self.snapshot: tuple[StatusSnapshotMetadata, bytes] | None = None
        self.snapshot_payload: dict[str, Any] | None = None
        self.snapshot_signature: str | None = None
        self.generation = 0
        self.build_count = 0
        self.encode_count = 0
        self.invalidation_reason = "startup"
        self.last_error = ""
        self.inventory: tuple[dict[str, object], bytes] | None = None
        self.inventory_generation = 0
        self.inventory_signature: str | None = None
        self.refresh_worker: threading.Thread | None = None

    def _sessions(self, request: dict[str, Any]) -> tuple[str, ...]:
        raw = request.get("sessions", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list) or len(raw) > STATUSD_MAX_SESSIONS:
            raise StatusProtocolError("invalid sessions")
        names = tuple(dict.fromkeys(str(item).strip() for item in raw if isinstance(item, str) and item.strip()))
        if len(names) != len(raw):
            raise StatusProtocolError("invalid sessions")
        return names

    def _ensure_app(self, sessions: tuple[str, ...]) -> TmuxWebtermApp:
        if self.app is None:
            self.app = TmuxWebtermApp(list(sessions), status_service_mode=True)
        self.app.sessions = list(sessions)
        self.session_names = sessions
        return self.app

    def _build(self, sessions: tuple[str, ...]) -> tuple[StatusSnapshotMetadata, bytes]:
        app = self._ensure_app(sessions)
        timings: dict[str, float] = {}
        payload, status = app.build_auto_approve_status(timings=timings, sync_workers=False)
        if not isinstance(payload, dict):
            raise StatusProtocolError("invalid status payload")
        payload["timings"] = timings
        source_payload = {key: value for key, value in payload.items() if key != "timings"}
        source_signature = hashlib.sha1(
            json.dumps(source_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        # Reserve the generation before encoding so the immutable body forwarded to every
        # consumer identifies the exact statusd snapshot it represents. Do not advance the
        # public counter until this body is committed: waiters must never observe a generation
        # for a snapshot that cannot yet be read.
        with self.lock:
            if self.snapshot is not None and not self.invalidation_reason and source_signature == self.snapshot_signature:
                return self.snapshot
            generation = self.generation + 1
        payload["agent_window_snapshot_revision"] = generation
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.lock:
            self.generation = generation
            self.build_count += 1
            self.encode_count += 1
            metadata = StatusSnapshotMetadata(
                generation=self.generation,
                status=int(status),
                stale=False,
                built_at=time.time(),
            )
            self.snapshot = (metadata, body)
            self.snapshot_payload = payload
            self.snapshot_signature = source_signature
            self.invalidation_reason = ""
            self.last_error = ""
            self.lock.notify_all()
        return metadata, body

    def _discover_roster(self, hint: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
        # The daemon owns the canonical roster; a web-supplied hint is only a
        # fallback when tmux enumeration fails, never authority.
        names, error = list_tmux_session_names()
        if error or not names:
            return hint, "hint"
        roster = tuple(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
        return (roster, "daemon") if roster else (hint, "hint")

    def _session_inventory_entry(self, info: Any) -> dict[str, Any]:
        # Bounded identifiers only: no git/transcript/repo enrichment. discover_status_sessions
        # runs with enrich_paths=False, so current_path here is the raw tmux pane cwd.
        panes = [
            {"target": str(pane.target or ""), "window": str(pane.window or ""), "cwd": str(pane.current_path or ""), "active": bool(getattr(pane, "active", False))}
            for pane in getattr(info, "panes", [])
        ]
        agents = [
            {"kind": str(agent.kind or ""), "pane": str(getattr(agent, "pane_target", "") or "")}
            for agent in getattr(info, "agents", [])
        ]
        material = json.dumps({"panes": panes, "agents": agents}, sort_keys=True, separators=(",", ":"))
        return {
            "windows": len({pane["window"] for pane in panes}),
            "panes": panes,
            "agents": agents,
            "source_signature": hashlib.sha1(material.encode("utf-8")).hexdigest()[:16],
        }

    def _inventory(self, request: dict[str, Any]) -> tuple[dict[str, object], bytes]:
        hint = self._sessions(request)
        roster, roster_source = self._discover_roster(hint)
        infos, errors = discover_status_sessions(list(roster))
        sessions_payload = {name: self._session_inventory_entry(info) for name, info in infos.items()}
        overall = hashlib.sha1(
            json.dumps({name: entry["source_signature"] for name, entry in sorted(sessions_payload.items())}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.lock:
            if overall != self.inventory_signature:
                self.inventory_generation += 1
                self.inventory_signature = overall
                self.lock.notify_all()
            generation = self.inventory_generation
            payload = {
                "inventory_generation": generation,
                "roster": list(roster),
                "roster_source": roster_source,
                "sessions": sessions_payload,
                "errors": list(errors),
            }
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            metadata = {"ok": True, "protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": generation, "built_at": time.time()}
            self.inventory = (metadata, body)
        return metadata, body

    def _snapshot(self, request: dict[str, Any]) -> tuple[dict[str, object], bytes]:
        sessions = self._sessions(request)
        session = request.get("session")
        if session is not None and (not isinstance(session, str) or not session):
            raise StatusProtocolError("invalid session")
        with self.build_lock:
            with self.lock:
                reusable = self.snapshot if sessions == self.session_names and not self.invalidation_reason else None
                if reusable is not None and time.time() - reusable[0].built_at > STATUSD_SNAPSHOT_MAX_AGE_SECONDS:
                    reusable = None
            if reusable is None:
                try:
                    metadata, body = self._build(sessions)
                except Exception as error:
                    with self.lock:
                        self.last_error = str(error)[:256]
                        retained = self.snapshot
                    if retained is None:
                        return {"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""
                    metadata, body = retained
                    return {"ok": True, **StatusSnapshotMetadata(metadata.generation, metadata.status, True, metadata.built_at, metadata.content_type).to_dict()}, body
            else:
                metadata, body = reusable
        if session is not None:
            with self.lock:
                payload = self.snapshot_payload
            if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), dict) or session not in payload["sessions"]:
                return {"ok": False, "status": int(HTTPStatus.NOT_FOUND), "error": "unknown session"}, b""
            session_payload = payload["sessions"][session]
            if not isinstance(session_payload, dict):
                return {"ok": False, "status": int(HTTPStatus.NOT_FOUND), "error": "unknown session"}, b""
            # Session-scoped reads are still statusd snapshots; retain the source revision so
            # a client cannot merge this state with Tabber data from a different generation.
            body = json.dumps({**session_payload, "agent_window_snapshot_revision": metadata.generation}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {"ok": True, **metadata.to_dict()}, body

    def _wait_generation(self, request: dict[str, Any]) -> tuple[dict[str, object], bytes]:
        after = int(request.get("after_generation") or 0)
        timeout = float(request.get("timeout_seconds") or 0.0)
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.generation <= after and timeout > 0 and not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.lock.wait(remaining)
            metadata = self.snapshot[0] if self.snapshot else None
        return {"ok": True, "protocol_version": STATUSD_PROTOCOL_VERSION, "generation": metadata.generation if metadata else 0, "changed": bool(metadata and metadata.generation > after)}, b""

    def refresh_loop(self) -> None:
        """Rebuild only while a web process holds a status generation lease.

        A worker-to-idle transition has no reliable immediate producer today.
        This daemon-owned cadence is therefore a correctness reconciliation, not
        a per-web polling substitute; each web process long-waits its generation.
        """
        while not self.stop_event.is_set():
            with self.lock:
                while not self.stop_event.is_set() and (not self.leases or self.snapshot is None):
                    self.lock.wait(0.1)
                if self.stop_event.is_set():
                    return
                metadata, _body = self.snapshot
                invalidated = bool(self.invalidation_reason)
                remaining = 0.0 if invalidated else max(0.0, STATUSD_SNAPSHOT_MAX_AGE_SECONDS - (time.time() - metadata.built_at))
                if remaining > 0:
                    self.lock.wait(remaining)
                    continue
                sessions = list(self.session_names)
            try:
                self._snapshot({"sessions": sessions})
            except (OSError, RuntimeError, StatusProtocolError) as error:
                with self.lock:
                    self.last_error = str(error)[:256]

    def start_refresh_worker(self) -> None:
        with self.lock:
            worker = self.refresh_worker
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(target=self.refresh_loop, name="statusd-refresh", daemon=True)
            self.refresh_worker = worker
        worker.start()

    def idle_due(self) -> bool:
        with self.lock:
            # A test worker or a crashed web process cannot release its lease. Reap before
            # deciding idleness so its abandoned lease cannot pin a private daemon forever.
            reap_dead_client_leases(self.leases)
            return not self.leases and time.monotonic() - self.last_client_at >= self.idle_seconds

    def status(self) -> dict[str, object]:
        with self.lock:
            snapshot = self.snapshot[0] if self.snapshot else None
            return {
                "ok": True, "service": STATUSD_SERVICE_NAME, "pid": os.getpid(), "version": STATUSD_PROTOCOL_VERSION, "code_revision": STATUSD_CODE_REVISION, "build_revision": 1,
                "socket": str(self.socket_path), "started_at": self.started_at, "clients": len(self.leases),
                "generation": self.generation, "build_count": self.build_count, "encode_count": self.encode_count,
                "inventory_generation": self.inventory_generation,
                "cache": {"ready": snapshot is not None, "stale": False}, "invalidation_reason": self.invalidation_reason,
                "last_error": self.last_error, "sessions": len(self.session_names), "queue_depth": 0,
            }

    def handle(self, request: dict[str, Any], _payload: bytes = b"") -> tuple[dict[str, object], bytes]:
        self.last_client_at = time.monotonic()
        try:
            request = validate_request(request)
        except StatusProtocolError as error:
            return {"ok": False, "error": str(error), "required_protocol_version": STATUSD_PROTOCOL_VERSION}, b""
        action = str(request["action"])
        if action == "ping":
            return {"ok": True, "service": STATUSD_SERVICE_NAME, "pid": os.getpid(), "version": STATUSD_PROTOCOL_VERSION, "code_revision": STATUSD_CODE_REVISION, "build_revision": 1}, b""
        if action in {"status", "profile"}:
            return self.status(), b""
        if action == "snapshot":
            try:
                return self._snapshot(request)
            except StatusProtocolError as error:
                return {"ok": False, "status": int(HTTPStatus.BAD_REQUEST), "error": str(error)}, b""
        if action == "inventory":
            try:
                return self._inventory(request)
            except StatusProtocolError as error:
                return {"ok": False, "status": int(HTTPStatus.BAD_REQUEST), "error": str(error)}, b""
        if action == "wait_generation":
            return self._wait_generation(request)
        if action == "invalidate":
            with self.lock:
                self.invalidation_reason = str(request.get("reason") or "external")[:80]
                self.lock.notify_all()
            return {"ok": True, "generation": self.generation}, b""
        if action == "lease":
            with self.lock:
                response = acquire_client_lease(self.leases, request.get("client_pid"), request.get("lease_id"))
                self.lock.notify_all()
            return {**response, "version": STATUSD_PROTOCOL_VERSION}, b""
        if action == "release":
            with self.lock:
                response = release_client_lease(self.leases, request.get("lease_id"))
                self.lock.notify_all()
            return response, b""
        if action == "shutdown":
            self.stop_event.set()
            with self.lock:
                self.lock.notify_all()
            return {"ok": True, "shutdown": True}, b""
        if action == "shutdown_if_idle":
            with self.lock:
                leased = bool(self.leases)
            if leased:
                return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
            self.stop_event.set()
            with self.lock:
                self.lock.notify_all()
            return {"ok": True, "shutdown": True}, b""
        return {"ok": False, "error": "unknown status action"}, b""

    def run(self) -> int:
        self.start_refresh_worker()
        return run_local_rpc_service(
            socket_path=self.socket_path, lock_path=self.lock_path, service_name=STATUSD_SERVICE_NAME,
            stop_event=self.stop_event, handle=self.handle,
            on_idle=self.idle_due,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
            concurrent_handlers=STATUSD_CONCURRENT_HANDLER_LIMIT,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux shared status service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=STATUSD_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    return PersistentStatusService(Path(args.socket), idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())

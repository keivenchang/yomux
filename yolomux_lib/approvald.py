"""Target-keyed approval worker service.

``approvald`` owns live ``AutoApproveWorker`` threads for all YOLOmux web
processes that share a state directory.  Web processes keep authentication,
session discovery, and status rendering; this service owns target locks and the
poll/classify/act/verify loop.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import common
from .auto_approve_worker import AutoApproveWorker
from .common import EVENT_LOG_PATH
from .events import EventLog
from .local_services.client import LocalServiceClient
from .local_services.rpc import LOCAL_RPC_VERSION
from .local_services.rpc import safe_socket_path
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import apply_service_process_priority
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service
from .settings import default_settings
from .settings import settings_payload


APPROVALD_PROTOCOL_VERSION = LOCAL_RPC_VERSION
APPROVALD_DEFAULT_IDLE_SECONDS = 60.0
APPROVALD_SOCKET_NAME = "approvald.sock"
APPROVALD_STATUS_TARGET_LIMIT = 256


def default_socket_path() -> Path:
    return safe_socket_path(common.STATE_DIR / "services" / APPROVALD_SOCKET_NAME, prefix="yolomux-approvald")


def approval_interval_seconds() -> float:
    defaults = default_settings()
    default = float(defaults.get("performance", {}).get("auto_approve_interval_seconds", 0.5))
    performance = settings_payload().get("settings", {}).get("performance", {})
    value = performance.get("auto_approve_interval_seconds", default) if isinstance(performance, dict) else default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.1, min(4.0, seconds))


def approval_prompt_source() -> str:
    settings = settings_payload().get("settings", {})
    yolo = settings.get("yolo") if isinstance(settings, dict) else {}
    value = yolo.get("prompt_source") if isinstance(yolo, dict) else None
    if value in {"pane", "hybrid"}:
        return str(value)
    return "hybrid"


@dataclass
class ApprovalWorkerRecord:
    session: str
    worker: AutoApproveWorker


class PersistentApprovalService:
    """One shared owner for target-keyed approval workers."""

    def __init__(self, socket_path: Path, idle_seconds: float = APPROVALD_DEFAULT_IDLE_SECONDS):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-approvald")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = multiprocessing.get_context("spawn").Event()
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.started_at = time.time()
        self.last_client_at = time.monotonic()
        self.leases: dict[str, int] = {}
        self.records: dict[str, ApprovalWorkerRecord] = {}
        self.event_log = EventLog(EVENT_LOG_PATH)

    def _event_callback(self, session: str, target: str):
        def callback(_target: str, event_type: str, message: str, details: dict[str, Any]) -> None:
            event_details = dict(details)
            message_key = str(event_details.pop("message_key", "") or "")
            message_params = event_details.pop("message_params", None)
            event_details["target"] = target
            self.event_log.append(
                session,
                event_type,
                message,
                event_details,
                message_key=message_key,
                message_params=message_params if isinstance(message_params, dict) else None,
            )

        return callback

    def _prune(self) -> None:
        for target, record in list(self.records.items()):
            if not record.worker.alive():
                self.records.pop(target, None)

    def _status_payload(self, target: str, record: ApprovalWorkerRecord | None = None) -> dict[str, Any]:
        item = record or self.records.get(target)
        if item is None:
            return {"target": target, "enabled": False, "approved": 0, "blocked": 0}
        payload = dict(item.worker.status())
        payload["session"] = item.session
        return payload

    def _start_worker(self, request: dict[str, Any]) -> dict[str, Any]:
        session = str(request.get("session") or "").strip()
        target = str(request.get("target") or session).strip()
        if not session or not target:
            return {"ok": False, "error": "session and target are required"}
        existing = self.records.get(target)
        if existing is not None and existing.worker.alive():
            existing.session = session
            return {"ok": True, "started": False, "status": self._status_payload(target, existing)}
        if existing is not None:
            self.records.pop(target, None)
        owner_extra = request.get("owner_extra") if isinstance(request.get("owner_extra"), dict) else {}
        owner_payload = {str(key): value for key, value in owner_extra.items() if isinstance(key, str)}
        owner_payload["session"] = session
        worker = AutoApproveWorker(
            target,
            interval=approval_interval_seconds(),
            event_callback=self._event_callback(session, target),
            owner_extra=owner_payload,
            dangerously_yolo=bool(request.get("dangerously_yolo")),
            prompt_source=approval_prompt_source(),
        )
        started, owner = worker.start()
        if not started:
            return {"ok": False, "locked": True, "owner": owner, "status": worker.status()}
        self.records[target] = ApprovalWorkerRecord(session=session, worker=worker)
        self.last_client_at = time.monotonic()
        return {"ok": True, "started": True, "status": self._status_payload(target)}

    def _stop_target(self, target: str) -> dict[str, Any]:
        record = self.records.pop(target, None)
        if record is None:
            return {"ok": True, "stopped": True, "target": target}
        stopped = record.worker.stop()
        if not stopped:
            self.records[target] = record
        self.last_client_at = time.monotonic()
        return {"ok": bool(stopped), "stopped": bool(stopped), "target": target, "status": self._status_payload(target, record)}

    def _stop_session(self, session: str) -> dict[str, Any]:
        targets = [target for target, record in self.records.items() if record.session == session]
        stopped = True
        statuses = []
        for target in targets:
            response = self._stop_target(target)
            stopped = bool(response.get("ok")) and stopped
            statuses.append(response)
        return {"ok": stopped, "session": session, "stopped": stopped, "targets": targets, "statuses": statuses}

    def status(self) -> dict[str, Any]:
        self._prune()
        targets = [
            self._status_payload(target, record)
            for target, record in sorted(self.records.items())[:APPROVALD_STATUS_TARGET_LIMIT]
        ]
        return {
            "ok": True,
            "service": "approvald",
            "pid": os.getpid(),
            "version": APPROVALD_PROTOCOL_VERSION,
            "socket": str(self.socket_path),
            "started_at": self.started_at,
            "clients": len(self.leases),
            "targets": targets,
            "target_count": len(self.records),
            "queues": {"latency": 0},
            "active_task": "",
            "cache": {},
            "generation": 0,
        }

    def handle(self, request: dict[str, Any], _payload: bytes = b"") -> tuple[dict[str, Any], bytes]:
        self.last_client_at = time.monotonic()
        action = str(request.get("action") or "")
        if action == "ping":
            return {"ok": True, "service": "approvald", "pid": os.getpid(), "version": APPROVALD_PROTOCOL_VERSION}, b""
        if action == "status":
            return self.status(), b""
        if action == "profile":
            return {"ok": True, "profile": self.status()}, b""
        if action == "drain":
            return {"ok": True, "drained": True, "targets": len(self.records)}, b""
        if action == "lease":
            response = acquire_client_lease(self.leases, request.get("client_pid"))
            return {**response, "version": APPROVALD_PROTOCOL_VERSION}, b""
        if action == "release":
            return release_client_lease(self.leases, request.get("lease_id")), b""
        if action == "start_worker":
            return self._start_worker(request), b""
        if action == "status_target":
            target = str(request.get("target") or "")
            self._prune()
            return {"ok": True, "status": self._status_payload(target)}, b""
        if action == "status_session":
            session = str(request.get("session") or "")
            self._prune()
            statuses = [self._status_payload(target, record) for target, record in sorted(self.records.items()) if record.session == session]
            return {"ok": True, "session": session, "statuses": statuses}, b""
        if action == "has_pending_prompt":
            target = str(request.get("target") or "")
            record = self.records.get(target)
            return {"ok": True, "pending": bool(record and record.worker.has_pending_prompt())}, b""
        if action == "alive":
            target = str(request.get("target") or "")
            record = self.records.get(target)
            return {"ok": True, "alive": bool(record and record.worker.alive())}, b""
        if action == "stop_target":
            return self._stop_target(str(request.get("target") or "")), b""
        if action == "stop_session":
            return self._stop_session(str(request.get("session") or "")), b""
        if action == "shutdown":
            for target in list(self.records):
                self._stop_target(target)
            self.stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        if action == "shutdown_if_idle":
            if self.leases or self.records:
                return {"ok": True, "shutdown": False, "leases": len(self.leases), "targets": len(self.records)}, b""
            self.stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        return {"ok": False, "error": f"unknown action: {action}"}, b""

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="approvald",
            stop_event=self.stop_event,
            handle=self.handle,
            on_idle=lambda: not self.leases and not self.records and time.monotonic() - self.last_client_at >= self.idle_seconds,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
            on_shutdown=self._shutdown,
        )

    def _shutdown(self) -> None:
        for target in list(self.records):
            self._stop_target(target)


class ApprovalWorkerHandle:
    """App-process proxy for a target owned by approvald."""

    def __init__(self, client: "ApprovalClient", target: str):
        self.client = client
        self.target = target

    def alive(self) -> bool:
        return bool(self.client.request({"action": "alive", "target": self.target}, timeout=0.3).get("alive"))

    def stop(self) -> bool:
        return bool(self.client.request({"action": "stop_target", "target": self.target}, timeout=2.5).get("ok"))

    def status(self) -> dict[str, Any]:
        response = self.client.request({"action": "status_target", "target": self.target}, timeout=0.5)
        status = response.get("status") if isinstance(response.get("status"), dict) else {}
        return status if isinstance(status, dict) else {"target": self.target, "enabled": False}

    @property
    def approved(self) -> int:
        return int(self.status().get("approved") or 0)

    @property
    def blocked(self) -> int:
        return int(self.status().get("blocked") or 0)

    @property
    def last_action(self) -> str:
        return str(self.status().get("last_action") or "")

    def has_pending_prompt(self) -> bool:
        return bool(self.client.request({"action": "has_pending_prompt", "target": self.target}, timeout=0.3).get("pending"))


class ApprovalClient(LocalServiceClient):
    """Thin cross-port client for target-keyed approval workers."""

    def __init__(self, socket_path: Path | None = None):
        super().__init__(
            "approvald",
            "yolomux_lib.approvald",
            socket_path or default_socket_path(),
            APPROVALD_PROTOCOL_VERSION,
            idle_seconds=APPROVALD_DEFAULT_IDLE_SECONDS,
            service_dir=Path(socket_path).parent if socket_path is not None else common.STATE_DIR / "services",
        )

    def start_worker(self, *, session: str, target: str, owner_extra: dict[str, Any], dangerously_yolo: bool) -> tuple[ApprovalWorkerHandle | None, dict[str, Any]]:
        if not self.ensure_started():
            return None, {"ok": False, "enabled": False, "error": "approvald unavailable", "target": target, "session": session}
        response = self.request(
            {
                "action": "start_worker",
                "session": session,
                "target": target,
                "owner_extra": owner_extra,
                "dangerously_yolo": bool(dangerously_yolo),
            },
            timeout=1.0,
        )
        status = response.get("status") if isinstance(response.get("status"), dict) else {}
        if response.get("ok"):
            return ApprovalWorkerHandle(self, target), dict(status)
        payload = dict(status)
        payload.update({"ok": False, "enabled": False, "target": target, "session": session, "locked": bool(response.get("locked")), "lock_owner": response.get("owner")})
        return None, payload

    def status_session(self, session: str) -> list[dict[str, Any]]:
        response = self.request({"action": "status_session", "session": session}, timeout=0.5)
        statuses = response.get("statuses") if isinstance(response.get("statuses"), list) else []
        return [status for status in statuses if isinstance(status, dict)]

    def stop_session(self, session: str) -> dict[str, Any]:
        return self.request({"action": "stop_session", "session": session}, timeout=2.5)

    def stop_target(self, target: str) -> dict[str, Any]:
        return self.request({"action": "stop_target", "target": target}, timeout=2.5)

    def has_pending_prompt(self, target: str) -> bool:
        return bool(self.request({"action": "has_pending_prompt", "target": target}, timeout=0.3).get("pending"))

    def service_status(self) -> dict[str, Any]:
        response = self.request({"action": "status"}, timeout=0.5)
        return response if isinstance(response, dict) else {}

    def runtime_status(self) -> dict[str, Any]:
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        return {
            "service": "approvald",
            "pid": int(payload.get("pid") or 0),
            "started_at": float(payload.get("started_at") or 0.0),
            "version": int(payload.get("version") or 0),
            "socket": str(payload.get("socket") or self.socket_path),
            "healthy": bool(status.get("healthy")),
            "clients": int(payload.get("clients") or 0),
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "active_task": str(payload.get("active_task") or ""),
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "generation": int(payload.get("generation") or 0),
            "target_count": int(payload.get("target_count") or 0),
            "resources": self.registry.resources(int(payload.get("pid") or 0)),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux approval worker service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=APPROVALD_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    return PersistentApprovalService(Path(args.socket), idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())

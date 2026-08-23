"""Target-keyed approval worker service.

``approvald`` owns live ``AutoApproveWorker`` threads for all YOLOmux web
processes that share a state directory.  Web processes keep authentication,
session discovery, and status rendering; this service owns target locks and the
poll/classify/act/verify loop.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import common
from .auto_approve_worker import AutoApproveWorker
from ..common import EVENT_LOG_PATH
from ..observability.events import EventLog
from ..local_services.client import LocalServiceClient
from ..local_services.command_router import CommonDaemonActions
from ..local_services.command_router import LocalServiceCommandRouter
from ..local_service_projection import registry_runtime_row
from ..local_services.rpc import LOCAL_RPC_VERSION
from ..local_services.rpc import safe_socket_path
from ..local_services.runtime import acquire_client_lease
from ..local_services.runtime import apply_service_process_priority
from ..local_services.runtime import claim_gated_idle_due
from ..local_services.runtime import LocalRpcServiceState
from ..local_services.runtime import release_client_lease
from ..local_services.runtime import run_local_rpc_service
from ..settings import default_settings
from ..settings import settings_payload


APPROVALD_PROTOCOL_VERSION = LOCAL_RPC_VERSION
APPROVALD_DEFAULT_IDLE_SECONDS = 60.0
APPROVALD_SOCKET_NAME = "approvald.sock"
APPROVALD_STATUS_TARGET_LIMIT = 256

APPROVALD_COMMAND_ROUTER = LocalServiceCommandRouter({
    "ping": "_handle_ping", "status": "_handle_status", "profile": "_handle_profile",
    "drain": "_handle_drain", "lease": "_handle_lease", "release": "_handle_release",
    "start_worker": "_handle_start_worker", "status_target": "_handle_status_target",
    "status_session": "_handle_status_session", "has_pending_prompt": "_handle_has_pending_prompt",
    "alive": "_handle_alive", "stop_target": "_handle_stop_target", "stop_session": "_handle_stop_session",
    "shutdown": "_handle_shutdown", "shutdown_if_idle": "_handle_shutdown_if_idle",
})


def default_socket_path() -> Path:
    return safe_socket_path(common.RUNTIME_DIR / "services" / APPROVALD_SOCKET_NAME, prefix="yolomux-approvald")


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


class PersistentApprovalService(LocalRpcServiceState):
    """One shared owner for target-keyed approval workers."""

    def __init__(self, socket_path: Path, idle_seconds: float = APPROVALD_DEFAULT_IDLE_SECONDS):
        super().__init__(socket_path, prefix="yolomux-approvald", idle_seconds=idle_seconds)
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
        # idle_due (claim_gated_idle_due) refreshes last_client_at on every
        # idle tick while self.records is non-empty; no per-mutation stamp
        # is needed here.
        return {"ok": True, "started": True, "status": self._status_payload(target)}

    def _stop_target(self, target: str) -> dict[str, Any]:
        record = self.records.pop(target, None)
        if record is None:
            return {"ok": True, "stopped": True, "target": target}
        stopped = record.worker.stop()
        if not stopped:
            self.records[target] = record
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
        recurring_rows = [item.get("recurring_work") for item in targets if isinstance(item.get("recurring_work"), dict)]
        recurring_work = {
            "class": "sample",
            "cadence_seconds": approval_interval_seconds(),
            "demanded": bool(self.records),
            "attempts": sum(int(row.get("attempts") or 0) for row in recurring_rows),
            "useful": sum(int(row.get("useful") or 0) for row in recurring_rows),
            "no_change": sum(int(row.get("no_change") or 0) for row in recurring_rows),
            "failures": sum(int(row.get("failures") or 0) for row in recurring_rows),
            "last_attempt_at": max((float(row.get("last_attempt_at") or 0.0) for row in recurring_rows), default=0.0),
            "last_useful_at": max((float(row.get("last_useful_at") or 0.0) for row in recurring_rows), default=0.0),
        }
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
            "recurring_work": recurring_work,
            "queues": {"latency": 0},
            "active_task": "",
            "cache": {},
            "generation": 0,
        }

    def _handle_ping(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return CommonDaemonActions.ping("approvald", APPROVALD_PROTOCOL_VERSION, pid=os.getpid())

    def _handle_status(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return CommonDaemonActions.status(self.status)

    def _handle_profile(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return CommonDaemonActions.status(self.status, profile=True)

    def _handle_drain(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return {"ok": True, "drained": True, "targets": len(self.records)}, b""

    def _handle_lease(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        response = acquire_client_lease(self.leases, request.get("client_pid"))
        return {**response, "version": APPROVALD_PROTOCOL_VERSION}, b""

    def _handle_release(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return release_client_lease(self.leases, request.get("lease_id")), b""

    def _handle_start_worker(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self._start_worker(request), b""

    def _handle_status_target(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        target = str(request.get("target") or "")
        self._prune()
        return {"ok": True, "status": self._status_payload(target)}, b""

    def _handle_status_session(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        session = str(request.get("session") or "")
        self._prune()
        statuses = [self._status_payload(target, record) for target, record in sorted(self.records.items()) if record.session == session]
        return {"ok": True, "session": session, "statuses": statuses}, b""

    def _handle_has_pending_prompt(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        record = self.records.get(str(request.get("target") or ""))
        return {"ok": True, "pending": bool(record and record.worker.has_pending_prompt())}, b""

    def _handle_alive(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        record = self.records.get(str(request.get("target") or ""))
        return {"ok": True, "alive": bool(record and record.worker.alive())}, b""

    def _handle_stop_target(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self._stop_target(str(request.get("target") or "")), b""

    def _handle_stop_session(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self._stop_session(str(request.get("session") or "")), b""

    def _handle_shutdown(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        for target in list(self.records):
            self._stop_target(target)
        self.stop_event.set()
        return {"ok": True, "shutdown": True}, b""

    def _handle_shutdown_if_idle(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        if self.leases or self.records:
            return {"ok": True, "shutdown": False, "leases": len(self.leases), "targets": len(self.records)}, b""
        self.stop_event.set()
        return {"ok": True, "shutdown": True}, b""

    def handle(self, request: dict[str, Any], payload: bytes = b"") -> tuple[dict[str, Any], bytes]:
        # Deliberately does NOT stamp last_client_at here, and the listener's
        # on_client callback (wired in run()) is a no-op.  Only idle_due
        # refreshes the clock, and only while a real claim (lease or worker
        # record) exists -- a diagnostic RPC, self-connected or external,
        # must never masquerade as demand.
        action = str(request.get("action") or "")
        response = APPROVALD_COMMAND_ROUTER.dispatch(self, action, request, payload)
        return response if response is not None else ({"ok": False, "error": f"unknown action: {action}"}, b"")

    def idle_due(self) -> bool:
        # claim_gated_idle_due is the one shared owner of the
        # transition/deadline algorithm every local service routes through;
        # approvald's claim predicate is a held lease or worker record.
        return claim_gated_idle_due(self, bool(self.leases) or bool(self.records))

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="approvald",
            stop_event=self.stop_event,
            handle=self.handle,
            on_idle=self.idle_due,
            on_client=lambda: None,
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
            service_dir=Path(socket_path).parent if socket_path is not None else common.RUNTIME_DIR / "services",
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

    def status_session_if_running(self, session: str) -> list[dict[str, Any]]:
        response = self.request_if_running({"action": "status_session", "session": session}, timeout=0.5)
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
        """Build approvald's whole System/health row.

        approvald is genuinely demand-scoped and declares it. The only path that creates it is
        `start_worker` (`:310`), reached when a session actually turns auto-approve on, plus the
        shared absent/refused recovery every client gets in
        `LocalServiceClient.request_with_binary` (`local_services/client.py:175-179`) when some
        route asks an already-wanted question. Nothing pins it up: its idle rule (`:252`) is
        `not self.leases and not self.records`, so with no worker record it retires itself after
        APPROVALD_DEFAULT_IDLE_SECONDS. On a machine with no auto-approve target configured that
        is its permanent, correct resting state, and calling it `down` would alarm forever.

        The dead-approver case the essential-service comment warns about is still covered, and
        is why this flag is read LAST by the health reducer: a worker that cannot reach approvald
        drives a start attempt, a failed start records a registry `failure_reason`, and a row
        carrying `last_failure` reduces to `down` no matter what this flag says.
        """
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        return registry_runtime_row("approvald", self.registry, status, payload, fields_before_failure={
            "demand_started": True,
            "socket": str(payload.get("socket") or self.socket_path),
            "clients": int(payload.get("clients") or 0),
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "active_task": str(payload.get("active_task") or ""),
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "generation": int(payload.get("generation") or 0),
            "target_count": int(payload.get("target_count") or 0),
        }, fields_after_failure={
            "recurring_work": payload.get("recurring_work") if isinstance(payload.get("recurring_work"), dict) else {},
        })


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

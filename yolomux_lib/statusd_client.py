"""Typed web-side client for the shared statusd service."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

from .infra import common
from .local_services.client import LocalServiceClient
from .local_services.rpc import safe_socket_path
from .statusd_protocol import STATUSD_PROTOCOL_VERSION
from .statusd_protocol import STATUSD_SERVICE_NAME
from .statusd_protocol import STATUSD_CODE_REVISION
from .statusd_protocol import activity_summary_disabled_response
from .statusd_protocol import activity_summary_enabled
from .statusd_protocol import encode_activity_work_body
from .statusd_protocol import stamped_request


STATUSD_SOCKET_NAME = "statusd.sock"
STATUSD_DEFAULT_IDLE_SECONDS = 60.0
STATUSD_ACTIVITY_SUMMARY_TIMEOUT_SECONDS = 60.0
STATUSD_WAIT_GENERATION_TRANSPORT_MARGIN_SECONDS = 0.5
STATUSD_GENERATION_PROBE_TRANSPORT_TIMEOUT_SECONDS = 1.5


def default_socket_path() -> Path:
    return safe_socket_path(common.RUNTIME_DIR / "services" / STATUSD_SOCKET_NAME, prefix="yolomux-statusd")


class StatusClient(LocalServiceClient):
    """Typed byte-forwarding client for the shared status owner."""

    def __init__(self, socket_path: Path | None = None):
        super().__init__(STATUSD_SERVICE_NAME, "yolomux_lib.statusd", socket_path or default_socket_path(), STATUSD_PROTOCOL_VERSION, idle_seconds=STATUSD_DEFAULT_IDLE_SECONDS, code_revision=STATUSD_CODE_REVISION, build_revision=1, service_dir=Path(socket_path).parent if socket_path is not None else common.RUNTIME_DIR / "services")

    def snapshot(self, sessions: list[str], session: str | None = None, timeout: float = 1.0) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""
        fields: dict[str, object] = {"sessions": list(sessions)}
        if session is not None:
            fields["session"] = session
        return self.request_with_binary(stamped_request("snapshot", **fields), timeout=timeout)

    def inventory(self, sessions_hint: list[str] | None = None, timeout: float = 1.0) -> tuple[dict[str, Any], bytes]:
        # The daemon owns roster discovery; sessions_hint is only a fallback bound.
        if not self.ensure_started():
            return {"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""
        fields: dict[str, object] = {}
        if sessions_hint is not None:
            fields["sessions"] = list(sessions_hint)
        return self.request_with_binary(stamped_request("inventory", **fields), timeout=timeout)

    def activity_summary(
        self,
        sessions: list[str],
        *,
        force: bool,
        locale: str,
        session_scope: str,
        hours: float,
        work_by_session: dict[str, dict[str, Any]],
        timeout: float = STATUSD_ACTIVITY_SUMMARY_TIMEOUT_SECONDS,
    ) -> tuple[dict[str, Any], bytes]:
        if not activity_summary_enabled():
            return activity_summary_disabled_response()
        if not self.ensure_started():
            return {"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""
        request_binary = encode_activity_work_body(work_by_session, sessions)
        return self.request_with_binary(
            stamped_request(
                "activity_summary",
                sessions=list(sessions),
                force=bool(force),
                locale=locale,
                session_scope=session_scope,
                hours=hours,
                work_by_session_binary=True,
            ),
            timeout=timeout,
            request_binary=request_binary,
        )

    def wait_generation(self, after_generation: int, timeout: float) -> dict[str, Any]:
        return self.request(
            stamped_request("wait_generation", after_generation=after_generation, timeout_seconds=timeout),
            timeout=timeout + STATUSD_WAIT_GENERATION_TRANSPORT_MARGIN_SECONDS,
        )

    def probe_generation(self, after_generation: int) -> dict[str, Any]:
        """Read the current generation without occupying a daemon handler between probes."""
        return self.request(
            stamped_request("wait_generation", after_generation=after_generation, timeout_seconds=0.0),
            timeout=STATUSD_GENERATION_PROBE_TRANSPORT_TIMEOUT_SECONDS,
        )

    def acquire_generation_lease(self) -> dict[str, Any]:
        """Keep statusd's demand-scoped generation refresher alive for one web process."""
        return self.registry.acquire_lease()

    def release_generation_lease(self, lease_id: str) -> dict[str, Any]:
        return self.registry.release_lease(lease_id)

    def invalidate(self, reason: str) -> dict[str, Any]:
        return self.request(stamped_request("invalidate", reason=str(reason)[:80]), timeout=0.25)

    def runtime_status(self) -> dict[str, Any]:
        runtime = self.registry.status()
        payload = runtime.get("status") if isinstance(runtime.get("status"), dict) else {}
        pid = int(payload.get("pid") or 0)
        return {
            "service": STATUSD_SERVICE_NAME,
            "pid": pid,
            "started_at": float(payload.get("started_at") or 0.0),
            "version": int(payload.get("version") or 0),
            "socket": str(payload.get("socket") or self.socket_path),
            "healthy": bool(runtime.get("healthy")),
            "clients": int(payload.get("clients") or 0),
            "queues": {"depth": int(payload.get("queue_depth") or 0)},
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "generation": int(payload.get("generation") or 0),
            "build_count": int(payload.get("build_count") or 0),
            "encode_count": int(payload.get("encode_count") or 0),
            "invalidation_reason": str(payload.get("invalidation_reason") or ""),
            "last_failure": str(payload.get("last_error") or runtime.get("failure_reason") or ""),
            "resources": self.registry.resources(pid),
        }

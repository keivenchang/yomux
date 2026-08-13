# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Typed web-side client for the shared watchd service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .infra import common
from .local_service_projection import registry_runtime_row
from .local_services.client import LocalServiceClient
from .local_services.rpc import safe_socket_path
from .watchd_protocol import WATCHD_CODE_REVISION
from .watchd_protocol import WATCHD_PROTOCOL_VERSION
from .watchd_protocol import WATCHD_SERVICE_NAME


WATCHD_SOCKET_NAME = "watchd.sock"
WATCHD_DEFAULT_IDLE_SECONDS = 60.0
WATCHD_TRANSPORT_MARGIN_SECONDS = 1.0
# watchd answers a long poll with state="reconfiguring" while it registers a
# native recursive watch, a call that holds its interpreter lock and cannot be
# interrupted. The next request is deliberately armed against a deadline that
# covers that declared window instead of reporting an unexplained transport
# timeout; the steady-state margin above is unchanged.
WATCHD_RECONFIGURE_TRANSPORT_MARGIN_SECONDS = 20.0
WATCHD_RECONFIGURING_STATE = "reconfiguring"
WATCHD_RECONFIGURE_MAX_BACKOFF_SECONDS = 1.0


def default_socket_path() -> Path:
    return safe_socket_path(common.RUNTIME_DIR / "services" / WATCHD_SOCKET_NAME, prefix="yolomux-watchd")


def stamped_request(action: str, **fields: object) -> dict[str, object]:
    return {"action": action, "protocol_version": WATCHD_PROTOCOL_VERSION, **fields}


class WatchClient(LocalServiceClient):
    """Bounded descriptor writer and revision waiter for watchd."""

    def __init__(self, socket_path: Path | None = None):
        requested = socket_path or default_socket_path()
        super().__init__(
            WATCHD_SERVICE_NAME,
            "yolomux_lib.watchd",
            requested,
            WATCHD_PROTOCOL_VERSION,
            idle_seconds=WATCHD_DEFAULT_IDLE_SECONDS,
            code_revision=WATCHD_CODE_REVISION,
            build_revision=1,
            service_dir=Path(socket_path).parent if socket_path is not None else common.RUNTIME_DIR / "services",
        )

    def acquire_lease(self, existing_lease_id: str = "") -> dict[str, Any]:
        return self.registry.acquire_lease(existing_lease_id)

    def release_lease(self, lease_id: str) -> dict[str, Any]:
        return self.registry.release_lease(lease_id)

    @staticmethod
    def transport_margin(reconfiguring: bool = False) -> float:
        """Return the one margin every watchd request reserves for its window."""
        return WATCHD_RECONFIGURE_TRANSPORT_MARGIN_SECONDS if reconfiguring else WATCHD_TRANSPORT_MARGIN_SECONDS

    @staticmethod
    def response_is_reconfiguring(response: dict[str, Any]) -> bool:
        return response.get("ok") is True and str(response.get("state") or "") == WATCHD_RECONFIGURING_STATE

    @staticmethod
    def reconfigure_backoff_seconds(response: dict[str, Any]) -> float:
        """Honour the daemon's declared retry hint without trusting it blindly."""
        hint = response.get("retry_after_seconds")
        if isinstance(hint, bool) or not isinstance(hint, (int, float)):
            return WATCHD_RECONFIGURE_MAX_BACKOFF_SECONDS
        return max(0.0, min(WATCHD_RECONFIGURE_MAX_BACKOFF_SECONDS, float(hint)))

    def upsert(self, lease_id: str, descriptor_id: str, descriptor: dict[str, Any], *, reconfiguring: bool = False) -> dict[str, Any]:
        return self.request(
            stamped_request("upsert", lease_id=lease_id, descriptor_id=descriptor_id, descriptor=descriptor),
            timeout=self.transport_margin(reconfiguring),
        )

    def remove(self, lease_id: str, descriptor_id: str, *, reconfiguring: bool = False) -> dict[str, Any]:
        return self.request(
            stamped_request("remove", lease_id=lease_id, descriptor_id=descriptor_id),
            timeout=self.transport_margin(reconfiguring),
        )

    @classmethod
    def long_poll_transport_timeout(cls, server_timeout: float, reconfiguring: bool = False) -> float:
        return server_timeout + cls.transport_margin(reconfiguring)

    def wait_revision(self, epoch: str, after_revision: int, timeout: float = 20.0, *, reconfiguring: bool = False) -> dict[str, Any]:
        return self.request(
            stamped_request("wait_revision", epoch=epoch, after_revision=after_revision, timeout_seconds=timeout),
            timeout=self.long_poll_transport_timeout(timeout, reconfiguring),
        )

    @staticmethod
    def _validate_product_length(metadata: dict[str, Any], body: bytes) -> tuple[dict[str, Any], bytes]:
        if metadata.get("state") != "ready":
            return metadata, body
        product = metadata.get("product") if isinstance(metadata.get("product"), dict) else {}
        length = product.get("length")
        if isinstance(length, bool) or not isinstance(length, int) or length != len(body):
            return {"ok": False, "state": "failed", "status": 502, "error": "watchd product length mismatch", "error_code": "producer_failed"}, b""
        return metadata, body

    def snapshot(self, since: str = "", force_full: bool = False, timeout: float = 0.5) -> tuple[dict[str, Any], bytes]:
        metadata, body = self.request_with_binary(
            stamped_request("snapshot", since=str(since or ""), force_full=bool(force_full)),
            timeout=timeout,
        )
        if metadata.get("ok") is not True:
            return metadata, body
        return self._validate_product_length(metadata, body)

    def snapshot_product(self, producer_id: str, timeout: float = 10.0) -> tuple[dict[str, Any], bytes]:
        metadata, body = self.request_with_binary(
            stamped_request("snapshot_product", producer_id=str(producer_id), timeout_seconds=timeout),
            timeout=self.long_poll_transport_timeout(timeout),
        )
        if metadata.get("ok") is not True:
            return metadata, body
        return self._validate_product_length(metadata, body)

    def runtime_status(self) -> dict[str, Any]:
        runtime = self.registry.status()
        payload = runtime.get("status") if isinstance(runtime.get("status"), dict) else {}
        return registry_runtime_row(WATCHD_SERVICE_NAME, self.registry, runtime, payload, fields_before_failure={
            "epoch": str(payload.get("epoch") or ""),
            "revision": int(payload.get("revision") or 0),
            "watch_generation": int(payload.get("watch_generation") or 0),
            "active_watch_generation": int(payload.get("active_watch_generation") or 0),
            "clients": int(payload.get("clients") or 0),
            "descriptors": int(payload.get("descriptors") or 0),
            "roots": int(payload.get("roots") or 0),
            "fallback": bool(payload.get("fallback")),
        })

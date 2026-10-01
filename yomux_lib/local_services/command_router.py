# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Explicit command routing shared by local RPC daemons."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Callable
from typing import Any

from .protocol_types import BinaryResponse
from .protocol_types import CommandHandler
from .protocol_types import JsonObject


CommandResponse = BinaryResponse


class LocalServiceCommandRouter:
    """Resolve a fixed action vocabulary to named owner methods."""

    def __init__(self, handlers: Mapping[str, str]) -> None:
        self._handlers = dict(handlers)
        if not self._handlers or any(not action or not method for action, method in self._handlers.items()):
            raise ValueError("local-service command routes must be non-empty")

    @property
    def actions(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def dispatch(self, owner: object, action: str, request: JsonObject, body: bytes) -> CommandResponse | None:
        method_name = self._handlers.get(action)
        if method_name is None:
            return None
        handler: CommandHandler = getattr(owner, method_name)
        return handler(request, body)


class CommonDaemonActions:
    """Pure response builders for genuinely identical daemon commands."""

    @staticmethod
    def ping(service: str, version: int, *, pid: int, **fields: Any) -> CommandResponse:
        return {"ok": True, "service": service, "pid": pid, "version": version, **fields}, b""

    @staticmethod
    def status(status_reader: Callable[[], dict[str, Any]], *, profile: bool = False) -> CommandResponse:
        status = status_reader()
        return ({"ok": True, "profile": status} if profile else status), b""

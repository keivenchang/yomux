# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Static-only shapes for the shared local-service protocol boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal
from typing import Protocol
from typing import TypedDict
from typing import TypeAlias


JsonObject: TypeAlias = dict[str, Any]
BinaryResponse: TypeAlias = tuple[JsonObject, bytes]


class PingRequest(TypedDict):
    action: Literal["ping"]
    protocol_version: int


class StatusRequest(TypedDict):
    action: Literal["status"]
    protocol_version: int


class LeaseRequest(TypedDict):
    action: Literal["lease"]
    protocol_version: int
    client_pid: int
    lease_id: str


class ReleaseRequest(TypedDict):
    action: Literal["release"]
    protocol_version: int
    lease_id: str


class ShutdownRequest(TypedDict):
    action: Literal["shutdown", "shutdown_if_idle"]
    protocol_version: int


CommonRequest: TypeAlias = PingRequest | StatusRequest | LeaseRequest | ReleaseRequest | ShutdownRequest


class SuccessfulResponse(TypedDict, total=False):
    ok: Literal[True]
    service: str
    pid: int
    version: int
    shutdown: bool


class FailedResponse(TypedDict, total=False):
    ok: Literal[False]
    error: str
    error_code: str
    status: str
    terminal: bool


CommonResponse: TypeAlias = SuccessfulResponse | FailedResponse


class Clock(Protocol):
    def __call__(self) -> float: ...


class RpcOperation(Protocol):
    def __call__(
        self,
        socket_path: str | Path,
        envelope: Any,
        *,
        binary: bytes = b"",
        timeout_seconds: float = 2.0,
        fallback_legacy: bool = False,
        probe: bool = False,
    ) -> BinaryResponse: ...


class LocalServiceClientBoundary(Protocol):
    def request_with_binary(
        self,
        payload: JsonObject,
        timeout: float = 0.5,
        request_binary: bytes = b"",
        *,
        probe: bool = False,
    ) -> BinaryResponse: ...

    def request(self, payload: JsonObject, timeout: float = 0.5, *, probe: bool = False) -> JsonObject: ...

    def ensure_started(self) -> bool: ...


class CommandHandler(Protocol):
    def __call__(self, request: JsonObject, body: bytes) -> BinaryResponse: ...

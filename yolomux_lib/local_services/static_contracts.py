# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Compile-time conformance checks for migrated local-service boundaries."""

from __future__ import annotations

from time import monotonic

from .client import LocalServiceClient
from .protocol_types import Clock
from .protocol_types import LocalServiceClientBoundary
from .protocol_types import RpcOperation
from .rpc import request


clock_boundary: Clock = monotonic
rpc_operation_boundary: RpcOperation = request


def require_client_boundary(client: LocalServiceClient) -> LocalServiceClientBoundary:
    return client

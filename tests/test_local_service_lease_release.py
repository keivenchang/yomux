# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
import threading
import time

from tests.helpers.external_lease_client import assert_daemon_refuses_a_self_lease
from tests.helpers.external_lease_client import external_lease_client
from yomux_lib.local_services import client as local_service_client
from yomux_lib.local_services import registry as local_service_registry
from yomux_lib.local_services import rpc
from yomux_lib.local_services.rpc import LocalRpcError
from yomux_lib.local_services.registry import LocalServiceRegistry
from yomux_lib.local_services.registry import LocalServiceSpec


def test_registry_lease_release_timeout_retries_the_typed_transport_failure(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 7),
    )
    calls = []
    settled = threading.Event()

    def request(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        settled.set()
        return {"ok": True, "leases": 0}, b""

    monkeypatch.setattr(local_service_registry, "request", request)

    owner = local_service_client.release_local_service_lease_eventually(
        registry.release_lease,
        "lease-timeout",
        retry_seconds=0.01,
    )

    assert settled.wait(timeout=1.0) is True
    assert len(calls) == 2
    assert owner.completed.is_set() is True
    assert owner.terminal_response is None
    assert owner._thread is not None


def test_registry_lease_release_protocol_failure_stops_without_retry(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 7),
    )
    calls = []
    emitted = []

    def request(*_args, **_kwargs):
        calls.append(True)
        raise LocalRpcError("unsupported RPC version")

    monkeypatch.setattr(local_service_registry, "request", request)
    monkeypatch.setattr(
        local_service_client,
        "emit_server_log",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )

    owner = local_service_client.release_local_service_lease_eventually(
        registry.release_lease,
        "lease-protocol",
        retry_seconds=0.01,
    )

    assert calls == [True]
    assert owner.completed.is_set() is True
    assert owner._thread is None
    assert owner.terminal_response is not None
    assert owner.terminal_response["ok"] is False
    assert owner.terminal_response["error"] == "unsupported RPC version"
    # Was the duplicate classifier's bare 'rpc' fallthrough, which collapsed every unnamed
    # OSError and LocalRpcError into one string. Routed through the single owner in rpc.py,
    # "unsupported RPC version" is the case that owner names exactly: a stale peer that needs
    # an upgrade, which a caller must be able to tell apart from a generic transport failure.
    assert owner.terminal_response["_transport_error"] == rpc.LOCAL_SERVICE_REASON_REVISION_MISMATCH
    assert owner.terminal_response["exception_type"] == "LocalRpcError"
    assert owner.terminal_response["cause"]["exception"] == {
        "type": "LocalRpcError",
        "message": "unsupported RPC version",
    }
    assert emitted[0][0][2] == "lease release stopped: unsupported rpc version"
    assert emitted[0][1]["delivery"] == "terminal"

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Incremental static gate for migrated local-service protocol boundaries."""

from __future__ import annotations

from typing import get_args

from tools import check_local_service_types
from yolomux_lib.local_services.protocol_types import CommonRequest
from yolomux_lib.local_services.protocol_types import CommonResponse


def test_common_protocol_unions_keep_request_and_response_variants_distinct():
    assert {variant.__name__ for variant in get_args(CommonRequest)} == {
        "PingRequest", "StatusRequest", "LeaseRequest", "ReleaseRequest", "ShutdownRequest",
    }
    assert {variant.__name__ for variant in get_args(CommonResponse)} == {
        "SuccessfulResponse", "FailedResponse",
    }


def test_static_slice_allowlist_is_an_exact_expansion_ratchet():
    assert check_local_service_types.static_slice() == (
        "yolomux_lib/local_services/protocol_types.py",
        "yolomux_lib/local_services/command_router.py",
        "yolomux_lib/local_services/rpc.py",
        "yolomux_lib/local_services/client.py",
        "yolomux_lib/local_services/registry.py",
        "yolomux_lib/local_services/static_contracts.py",
    )


def test_static_gate_fails_closed_when_mypy_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(check_local_service_types.shutil, "which", lambda _name: None)

    assert check_local_service_types.main() == 2
    assert capsys.readouterr().err == "local-service type gate requires mypy\n"

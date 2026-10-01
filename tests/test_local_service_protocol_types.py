# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Incremental static gate for migrated local-service protocol boundaries."""

from __future__ import annotations

import tempfile
from types import SimpleNamespace
from typing import get_args

from tools import check_local_service_types
from yomux_lib.local_services.protocol_types import CommonRequest
from yomux_lib.local_services.protocol_types import CommonResponse


def test_common_protocol_unions_keep_request_and_response_variants_distinct():
    assert {variant.__name__ for variant in get_args(CommonRequest)} == {
        "PingRequest", "StatusRequest", "LeaseRequest", "ReleaseRequest", "ShutdownRequest",
    }
    assert {variant.__name__ for variant in get_args(CommonResponse)} == {
        "SuccessfulResponse", "FailedResponse",
    }


def test_static_slice_allowlist_is_an_exact_expansion_ratchet():
    assert check_local_service_types.static_slice() == (
        "yomux_lib/local_services/protocol_types.py",
        "yomux_lib/local_services/command_router.py",
        "yomux_lib/local_services/rpc.py",
        "yomux_lib/local_services/client.py",
        "yomux_lib/local_services/registry.py",
        "yomux_lib/local_services/static_contracts.py",
    )


def test_static_gate_fails_closed_when_mypy_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(check_local_service_types.shutil, "which", lambda _name: None)

    assert check_local_service_types.main() == 2
    assert capsys.readouterr().err == "local-service type gate requires mypy\n"


def test_static_gate_uses_a_writable_ephemeral_mypy_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(check_local_service_types.shutil, "which", lambda _name: "/usr/bin/mypy")
    monkeypatch.setattr(
        check_local_service_types.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    assert check_local_service_types.main() == 0
    command, kwargs = calls[0]
    cache_index = command.index("--cache-dir")
    assert command[cache_index + 1].startswith(f"{tempfile.gettempdir()}/yolomux-mypy-")
    assert ".mypy_cache" not in command
    assert kwargs["cwd"] == check_local_service_types.REPO_ROOT

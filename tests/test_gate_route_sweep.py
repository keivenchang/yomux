# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Response-envelope and causal-error classifiers retained from the historical route sweep."""

from __future__ import annotations

import json
from typing import Any

import pytest


def _contract_envelope_kind(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    request = payload.get("request")
    if (
        state not in {"ready", "queued", "failed"}
        or not isinstance(request, dict)
        or not isinstance(request.get("id"), str)
        or not request["id"]
    ):
        return None
    if state == "ready":
        return "contract-ready" if "data" in payload and "operation" not in payload and "error" not in payload else None
    if state == "queued":
        operation = payload.get("operation")
        return (
            "contract-queued"
            if isinstance(operation, dict)
            and isinstance(operation.get("id"), str)
            and bool(operation["id"])
            and "data" not in payload
            and "error" not in payload
            else None
        )
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    stack = error.get("stack")
    if (
        not isinstance(error.get("code"), str)
        or not error["code"]
        or not isinstance(message, dict)
        or not isinstance(message.get("key"), str)
        or not message["key"]
        or not isinstance(message.get("fallback"), str)
        or not isinstance(message.get("params"), dict)
        or not isinstance(error.get("origin"), str)
        or not error["origin"]
        or not isinstance(error.get("retryable"), bool)
        or not isinstance(stack, list)
        or not stack
        or "data" in payload
        or "operation" in payload
    ):
        return None
    if not all(
        isinstance(frame, dict)
        and isinstance(frame.get("component"), str)
        and bool(frame["component"])
        and isinstance(frame.get("operation"), str)
        and bool(frame["operation"])
        and isinstance(frame.get("code"), str)
        and bool(frame["code"])
        for frame in stack
    ):
        return None
    return "contract-error"


def _legacy_typed_error_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    descriptor = payload.get("user_message")
    if isinstance(descriptor, dict):
        return (
            isinstance(payload.get("error"), str)
            and isinstance(descriptor.get("key"), str)
            and bool(descriptor.get("key"))
            and isinstance(descriptor.get("fallback"), str)
            and isinstance(descriptor.get("params"), dict)
        )
    return (
        isinstance(payload.get("status"), str)
        and bool(payload.get("status"))
        and isinstance(payload.get("reason") or payload.get("error") or payload.get("error_code"), str)
    )


def _response_envelope_kind(payload: Any, *, is_api_error: bool) -> str:
    contract_kind = _contract_envelope_kind(payload)
    if contract_kind is not None:
        return contract_kind
    if is_api_error:
        return "legacy-error" if _legacy_typed_error_payload(payload) else "untyped-error"
    if isinstance(payload, (dict, list)):
        return "legacy-json"
    return "non-json"


def _assert_sweep_passes(outcomes: list[dict[str, Any]]) -> None:
    failures = [outcome for outcome in outcomes if outcome.get("outcome") == "failed"]
    if failures:
        raise AssertionError(f"route sweep failures: {json.dumps(failures, sort_keys=True)}")


def _assert_route_sweep_batchd_transport_failure(entry: dict[str, Any]) -> None:
    event = str(entry.get("event") or "")
    request_id = str(entry.get("requestId") or "")
    message = str(entry.get("message") or "")
    assert entry.get("delivery") == "timeout" and event in {"produce", "product"}, entry
    assert entry.get("route") == "local-service:batchd" and request_id, entry
    assert f"action={event} request_id={request_id}" in message, entry
    assert "TimeoutError: timed out" in message, entry


def test_route_sweep_accepts_contract_errors_and_rejects_genuinely_untyped_errors() -> None:
    contract_error = {
        "state": "failed",
        "request": {"id": "r-fixture"},
        "error": {
            "code": "operation_not_found",
            "message": {
                "key": "common.notFound",
                "params": {},
                "fallback": "operation not found",
            },
            "origin": "server.http",
            "retryable": False,
            "details": {"operation_id": "route-sweep-missing"},
            "stack": [{
                "component": "server.http",
                "operation": "GET /api/operations/{id}",
                "code": "operation_not_found",
            }],
        },
    }
    assert _response_envelope_kind(contract_error, is_api_error=True) == "contract-error"

    untyped_error = {
        "state": "failed",
        "request": {"id": "r-fixture"},
        "error": {"message": "no typed code or causal stack"},
    }
    assert _response_envelope_kind(untyped_error, is_api_error=True) == "untyped-error"
    with pytest.raises(AssertionError, match="untyped-error"):
        _assert_sweep_passes([{
            "method": "GET",
            "route": "/api/synthetic-untyped",
            "outcome": "failed",
            "reason": "untyped-error",
            "response_envelope": "untyped-error",
        }])


@pytest.mark.parametrize("event", ("produce", "product"))
def test_route_sweep_accepts_only_correlated_batchd_timeout_phases(event: str) -> None:
    entry = {
        "delivery": "timeout",
        "event": event,
        "route": "local-service:batchd",
        "requestId": "fixture-batchd-request",
        "message": f"action={event} request_id=fixture-batchd-request\nTimeoutError: timed out",
    }

    _assert_route_sweep_batchd_transport_failure(entry)

    with pytest.raises(AssertionError):
        _assert_route_sweep_batchd_transport_failure({**entry, "event": "relay"})

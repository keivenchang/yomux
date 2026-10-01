# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared watch-diff payload assembly for daemon and compatibility callers."""

from __future__ import annotations

import copy
from http import HTTPStatus
from typing import Any


def responses_by_index(products: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    responses: dict[int, dict[str, Any]] = {}
    for product in products:
        for response in product.get("responses", []):
            if not isinstance(response, dict) or isinstance(response.get("id"), bool):
                continue
            try:
                response_id = int(response.get("id"))
            except (TypeError, ValueError):
                continue
            responses[response_id] = response
    return responses


def payload_from_products(
    base_payload: dict[str, Any],
    roots: list[str],
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    responses = responses_by_index(products)
    compute_ms = 0.0
    for product in products:
        performance = product.get("performance") if isinstance(product.get("performance"), dict) else {}
        compute_ms += max(0.0, float(performance.get("operation_ms") or 0.0))
    ordered_responses = [
        copy.deepcopy(responses.get(index) or {
            "id": index,
            "ok": False,
            "status": int(HTTPStatus.SERVICE_UNAVAILABLE),
            "error": "filesystem batch result missing",
        })
        for index in range(len(roots))
    ]
    directories: list[dict[str, Any]] = []
    summary = {
        "roots_requested": len(roots),
        "roots_listed": 0,
        "roots_error": 0,
        "entries_listed": 0,
        "files_listed": 0,
        "dirs_listed": 0,
    }
    for index, root in enumerate(roots):
        response = responses.get(index)
        if response is None or response.get("ok") is not True:
            failure = response or {}
            directories.append({
                "path": root,
                "status": int(failure.get("status") or HTTPStatus.SERVICE_UNAVAILABLE),
                "ok": False,
                "error": copy.deepcopy(failure.get("error") or "filesystem batch result missing"),
            })
            summary["roots_error"] += 1
            continue
        data = response.get("payload") if isinstance(response.get("payload"), dict) else {}
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        summary["entries_listed"] += len(entries)
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("kind") or "") == "dir":
                summary["dirs_listed"] += 1
            else:
                summary["files_listed"] += 1
        summary["roots_listed"] += 1
        directories.append({
            "path": root,
            "status": int(response.get("status") or HTTPStatus.OK),
            "ok": True,
            "data": copy.deepcopy(data),
        })
    return {
        **copy.deepcopy(base_payload),
        "roots": list(roots),
        "responses": ordered_responses,
        "directories": directories,
        "listing_summary": summary,
        "compute_ms": round(compute_ms, 1),
    }

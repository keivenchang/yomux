from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import DEFAULT_LINEAR_ISSUE_BASE_URL
from .common import HTTP_METADATA_TIMEOUT_SECONDS
from .common import LINEAR_API_URL
from .common import _CACHE_MISS
from .github_client import http_json


def linear_issue_metadata(identifier: str, cache: Any, allow_network: bool = True) -> dict[str, Any]:
    key = f"linear:{identifier}"
    cached = cache.get(key)
    if cached is not _CACHE_MISS:
        return cached
    if not allow_network:
        return fallback_linear_issue(identifier)
    value = linear_issue_from_api(identifier) or fallback_linear_issue(identifier)
    cache.set(key, value)
    return value


def linear_issue_from_api(identifier: str) -> dict[str, Any] | None:
    token = linear_key()
    if not token:
        return None
    payload = {
        "query": (
            "query($id: String!) { issue(id: $id) { "
            "identifier title url state { name } "
            "} }"
        ),
        "variables": {"id": identifier},
    }
    response = http_json(
        LINEAR_API_URL,
        headers={"Authorization": token, "Content-Type": "application/json"},
        payload=payload,
        timeout=HTTP_METADATA_TIMEOUT_SECONDS,
    )
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    issue = data.get("issue") if isinstance(data, dict) else None
    if not isinstance(issue, dict):
        return None
    state = issue.get("state")
    return {
        "identifier": issue.get("identifier") if isinstance(issue.get("identifier"), str) else identifier,
        "title": issue.get("title") if isinstance(issue.get("title"), str) else None,
        "state": state.get("name") if isinstance(state, dict) and isinstance(state.get("name"), str) else None,
        "url": issue.get("url") if isinstance(issue.get("url"), str) else linear_issue_url(identifier),
        "source": "linear-api",
    }


def fallback_linear_issue(identifier: str) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "title": None,
        "state": None,
        "url": linear_issue_url(identifier),
        "source": "local-id",
    }


def linear_issue_url(identifier: str) -> str:
    base_url = os.environ.get("YOLOMUX_LINEAR_ISSUE_BASE_URL", DEFAULT_LINEAR_ISSUE_BASE_URL).rstrip("/")
    return f"{base_url}/{quote(identifier)}"


def linear_key() -> str | None:
    token = os.environ.get("LINEAR_KEY")
    if token:
        return token.strip()
    path = Path.home() / ".config" / "linear.key"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None

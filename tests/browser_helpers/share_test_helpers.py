"""Small, reusable helpers for share route and browser fixtures."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import MutableSequence
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


def verify_share_token(record: dict[str, Any], seen_tokens: MutableSequence[str] | None = None, *, include_token: bool = False) -> Callable[[str], dict[str, Any] | None]:
    """Return the standard valid-token verifier, optionally retaining call evidence."""
    def verify(token: str) -> dict[str, Any] | None:
        if seen_tokens is not None:
            seen_tokens.append(token)
        if token != "valid-share-token":
            return None
        return record | {"token": token} if include_token else record
    return verify


def share_debug_url(url: str) -> str:
    """Enable the opt-in share diagnostics without changing any other URL field."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["shareDebug"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

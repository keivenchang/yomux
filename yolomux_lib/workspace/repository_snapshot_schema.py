# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Closed schema for cached session-files repository snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..filesystem.exclusions import path_exclusion_verdict


SNAPSHOT_FIELDS = frozenset({
    "branch",
    "statuses",
    "numstat",
    "file_identities",
    "selected_from",
    "selected_to",
    "status_error",
    "repo_error",
    "repo_error_message",
    "recent_refs",
    "ahead_behind",
})
STATUS_VALUES = frozenset({"A", "C", "D", "M", "R", "?"})
MESSAGE_FIELDS = frozenset({"key", "params", "fallback"})
REF_REQUIRED_FIELDS = frozenset({"ref", "short", "subject"})
REF_OPTIONAL_FIELDS = frozenset({"aliases", "author", "commit", "date"})


def repository_snapshot_admits_path(repo: Path, relative_path: str) -> bool:
    """Apply filesystem policy before Git metadata can enter a snapshot or cache."""

    candidate = Path(relative_path)
    if not relative_path or candidate.is_absolute() or ".." in candidate.parts:
        return False
    requested = repo / candidate
    return not path_exclusion_verdict(
        requested,
        configured_roots=(str(repo),),
        relative_to=repo,
    ).excluded


def _bounded_text(value: Any, *, allow_empty: bool = True) -> str | None:
    if not isinstance(value, str) or "\x00" in value or len(value) > 65536:
        return None
    if not allow_empty and not value:
        return None
    return value


def _nonnegative_count(value: Any) -> int | None | object:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _INVALID
    return value


_INVALID = object()


def _message_descriptor(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != MESSAGE_FIELDS:
        return None
    key = _bounded_text(value.get("key"))
    fallback = _bounded_text(value.get("fallback"))
    params = value.get("params")
    if key is None or fallback is None or not isinstance(params, dict):
        return None
    if any(not isinstance(name, str) or not isinstance(item, str) for name, item in params.items()):
        return None
    return {"key": key, "params": dict(params), "fallback": fallback}


def _recent_refs(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    allowed = REF_REQUIRED_FIELDS | REF_OPTIONAL_FIELDS
    for raw in value:
        if not isinstance(raw, dict) or not REF_REQUIRED_FIELDS <= set(raw) <= allowed:
            return None
        entry: dict[str, Any] = {}
        for field in REF_REQUIRED_FIELDS:
            text = _bounded_text(raw.get(field), allow_empty=False)
            if text is None:
                return None
            entry[field] = text
        for field in ("author", "commit"):
            if field in raw:
                text = _bounded_text(raw[field], allow_empty=False)
                if text is None:
                    return None
                entry[field] = text
        if "date" in raw:
            date = raw["date"]
            if not isinstance(date, str) or not date.isdigit():
                return None
            entry["date"] = date
        if "aliases" in raw:
            aliases = raw["aliases"]
            if not isinstance(aliases, list) or any(_bounded_text(alias, allow_empty=False) is None for alias in aliases):
                return None
            entry["aliases"] = list(aliases)
        result.append(entry)
    return result


def sanitize_repository_snapshot(repo: Path, snapshot: Any) -> dict[str, Any] | None:
    """Return an exact policy-admitted snapshot, or reject the complete record."""

    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        return None
    text_fields: dict[str, str] = {}
    for field in ("branch", "selected_from", "selected_to", "status_error", "repo_error"):
        text = _bounded_text(snapshot.get(field))
        if text is None:
            return None
        text_fields[field] = text
    raw_statuses = snapshot.get("statuses")
    if not isinstance(raw_statuses, dict):
        return None
    statuses: dict[str, str] = {}
    for relative_path, status in raw_statuses.items():
        if (
            not isinstance(relative_path, str)
            or not isinstance(status, str)
            or status not in STATUS_VALUES
            or not repository_snapshot_admits_path(repo, relative_path)
        ):
            return None
        statuses[relative_path] = status
    raw_numstat = snapshot.get("numstat")
    if not isinstance(raw_numstat, dict) or not set(raw_numstat) <= set(statuses):
        return None
    numstat: dict[str, dict[str, int | None]] = {}
    for relative_path, counts in raw_numstat.items():
        if not isinstance(counts, dict) or set(counts) != {"added", "removed"}:
            return None
        added = _nonnegative_count(counts.get("added"))
        removed = _nonnegative_count(counts.get("removed"))
        if added is _INVALID or removed is _INVALID:
            return None
        numstat[relative_path] = {"added": added, "removed": removed}
    raw_identities = snapshot.get("file_identities")
    if not isinstance(raw_identities, dict) or set(raw_identities) != set(statuses):
        return None
    file_identities: dict[str, list[int] | None] = {}
    for relative_path, identity in raw_identities.items():
        if identity is None:
            file_identities[relative_path] = None
            continue
        if (
            not isinstance(identity, list)
            or len(identity) != 2
            or any(isinstance(part, bool) or not isinstance(part, int) or part < 0 for part in identity)
        ):
            return None
        file_identities[relative_path] = [int(identity[0]), int(identity[1])]
    message = _message_descriptor(snapshot.get("repo_error_message"))
    recent_refs = _recent_refs(snapshot.get("recent_refs"))
    ahead_behind = snapshot.get("ahead_behind")
    if message is None or recent_refs is None or not isinstance(ahead_behind, dict):
        return None
    if ahead_behind and set(ahead_behind) != {"ahead", "behind"}:
        return None
    normalized_ahead_behind: dict[str, int] = {}
    for key, value in ahead_behind.items():
        count = _nonnegative_count(value)
        if count is _INVALID or count is None:
            return None
        normalized_ahead_behind[key] = count
    return {
        **text_fields,
        "statuses": statuses,
        "numstat": numstat,
        "file_identities": file_identities,
        "repo_error_message": message,
        "recent_refs": recent_refs,
        "ahead_behind": normalized_ahead_behind,
    }

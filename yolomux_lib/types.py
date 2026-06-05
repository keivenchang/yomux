# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""TypedDict schemas for the most-used API payload shapes.

Adding types here catches field-name drift across app.py, server.py, and
session_files.py at mypy/pyright time with zero runtime cost.
"""
from __future__ import annotations

from typing import Any
from typing import TypedDict


class SessionFileEntry(TypedDict, total=False):
    path: str
    relative_path: str
    repo: str
    agents: list[str]
    status: str
    mtime: float


class RepoPayload(TypedDict, total=False):
    repo: str
    count: int
    touched_count: int
    added: int
    removed: int


class SessionFilesPayload(TypedDict, total=False):
    session: str
    files: list[SessionFileEntry]
    repos: list[RepoPayload]
    errors: list[str]
    from_ref: str
    to_ref: str
    refs_by_repo: dict[str, list[dict[str, str]]]


class AutoApproveState(TypedDict):
    enabled: bool
    paths: list[str]
    commands: list[str]
    session: str


class RunHistoryEntry(TypedDict, total=False):
    session: str
    kind: str
    start: float
    end: float | None
    exit_code: int | None
    transcript: str | None
    summary: str | None

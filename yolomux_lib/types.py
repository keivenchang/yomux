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


class AutoApproveState(TypedDict, total=False):
    target: str
    session: str
    enabled: bool
    enabled_elsewhere: bool
    locked: bool
    approved: int
    blocked: int
    last_action: str
    error: str | None
    started_at: float
    lock_owner: dict[str, Any] | None
    prompt_source: str
    prompt: dict[str, Any]
    screen: dict[str, Any]
    agent_windows: list[dict[str, Any]]


class AutoApproveStatusPayload(TypedDict, total=False):
    session_order: list[str]
    sessions: dict[str, AutoApproveState]
    errors: list[str]
    rules: dict[str, Any]
    error: str


class SearchResultTarget(TypedDict, total=False):
    type: str
    session: str
    timestamp: str
    tab: str


class SearchResult(TypedDict, total=False):
    session: str
    timestamp: str
    kind: str
    source: str
    title: str
    snippet: str
    target: SearchResultTarget


class RunHistoryEntry(TypedDict, total=False):
    id: str
    session: str
    agent: dict[str, Any] | None
    prompt: str
    cwd: str
    tmux_target: str
    tmux_command: str
    started_at: str
    started_ts: float
    ended_at: str
    ended_ts: float
    final_state: str
    pr: dict[str, Any] | None
    latest_summary: str
    latest_summary_updated_ts: float
    transcript: str
    transcript_mtime: float
    project: dict[str, Any]
    recent_events: list[dict[str, Any]]


class RunHistoryPayload(TypedDict, total=False):
    session: str
    runs: list[RunHistoryEntry]
    errors: list[str]
    error: str

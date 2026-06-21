from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import shutil
import shlex
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from functools import wraps
from http import HTTPStatus
from pathlib import Path
from typing import Any
from typing import Callable
from urllib.parse import unquote
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from . import common
from . import filesystem
from . import session_files
from . import yolo_rules
from .approvals import blank_prompt_state
from .approvals import hybrid_approval_prompt_state
from .activity_summary import activity_signature
from .activity_summary import build_recent_agents_payload
from .activity_summary import build_global_activity_summary
from .activity_summary import build_session_activity_summary
from .activity_summary import build_yoagent_chat_prompt
from .activity_summary import build_yoagent_resume_prompt
from .activity_summary import deterministic_yoagent_reply
from .activity_summary import yoagent_capabilities_payload
from .activity_summary import yoagent_context_lines
from .activity_summary import yoagent_question_requests_work_next
from .auto_approve_worker import AutoApproveWorker
from .auto_approve_worker import auto_approve_lock_message
from .auto_approve_worker import auto_approve_lock_owner
from .atomic_file import atomic_write_text
from .atomic_file import file_lock
from .cache import MISS as CACHE_MISS
from .cache import TtlCache
from .client_events import ClientEventBroker
from .activity import ActivityLedger
from .common import ACTIVITY_HEARTBEATS_PATH
from .common import ACTIVITY_PATH
from .common import AGENT_COMMANDS
from .common import DEFAULT_UPLOAD_SUBDIR
from .common import EVENT_LOG_PATH
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_EVENT_TAIL_LINES
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import MAX_YOLOMUX_SESSION_TABS
from .common import PROJECT_ROOT
from .common import RUN_HISTORY_PATH
from .common import SERVER_HOSTNAME
from .common import SERVER_STARTED_AT
from .common import SUMMARY_MAX_PROMPT_CHARS
from .common import WATCH_INDEX_PATH
from .common import YOLOMUX_VERSION
from .common import UPLOAD_MAX_FILES
from .common import UPLOAD_MAX_BYTES
from .common import as_dict
from .common import next_numbered_session_name
from .common import tail_file_lines
from .common import truncate_text
from .control import YolomuxControlServer
from .control import send_yolomux_control_request
from .drop_actions import run_drop_action
from .events import EventLog
from .events import RunHistoryStore
from .events import search_snippet
from .events import read_yolomux_state
from .events import update_yolomux_state
from .agent_tui import classify_agent_pane
from .metadata import MetadataCache
from .metadata import candidate_session_cwds
from .metadata import focus_root_for_session
from .metadata import github_checks_unknown
from .metadata import project_inventory
from .metadata import pull_request_number_from_subject
from .metadata import session_git_inventory
from .metadata import session_project_metadata
from .metadata import session_to_json
from .metadata import watched_pr_metadata
from .sessions import active_window_for_panes
from .sessions import discover_sessions
from .settings import default_settings
from .settings import save_settings
from .settings import SETTINGS_PATH
from .settings import settings_payload
from .settings import summary_settings as normalized_summary_settings
from .transcripts import codex_summary_prompt
from .transcripts import codex_event_text
from .transcripts import compact_summary_lines
from .transcripts import compact_transcript_items
from .transcripts import compact_transcript_items_since
from .transcripts import compact_transcript_lines
from .transcripts import format_transcript_item
from .transcripts import newest_transcript_timestamp
from .transcripts import transcript_activity_is_recent
from .transcripts import transcript_delta_result_state
from .transcripts import transcript_run_metadata
from .transcripts import session_transcript_activity_state
from .transcripts import trim_prompt_text
from .prompt_detector import agent_screen_state
from .prompt_detector import approval_prompt_state
from .tmux_utils import cmd_error
from .tmux_utils import list_tmux_session_names
from .tmux_utils import tmux
from .tmux_utils import tmux_clear_input
from .tmux_utils import tmux_capture_pane
from .tmux_utils import tmux_capture_pane_styled
from .tmux_utils import tmux_has_exact_session
from .tmux_utils import tmux_paste_text
from .tmux_utils import tmux_session_target
from .tmux_signals import fetch_tmux_signal_snapshot
from .tmux_signals import TmuxSignalEventWatcher
from .types import AutoApproveState
from .types import AutoApproveStatusPayload
from .types import RunHistoryEntry
from .types import RunHistoryPayload
from .types import SearchResult
from .uploads import sanitize_upload_filename
from .uploads import unique_upload_path
from .web import server_string
from .workdir import agent_command
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_status
from .workdir import available_agent_commands
from .workdir import resolved_upload_dir
from .workdir import session_workdir
from .yoagent import backends as yoagent_backends
from .yoagent import conversation as yoagent_conversation
from .yoagent.actions import parse_yoagent_action_intent
from .yoagent.actions import parse_yoagent_job_intent
from .yoagent.actions import parse_yoagent_skill_file_intent
from .yoagent.actions import redacted_action_text
from .yoagent.actions import redacted_action_preview
from .yoagent.preferences import yoagent_operator_response
from .yoagent.preferences import parse_settings_read
from .yoagent.preferences import parse_settings_write
from .yoagent.preferences import product_state_needs_activity
from .yoagent.skills import delete_user_skill_file
from .yoagent.skills import list_user_skill_files
from .yoagent.skills import load_yoagent_skills
from .yoagent.skills import read_user_skill_file
from .yoagent.skills import write_user_skill_file
from .yoagent.transports import TMUX_LEGACY_TRANSPORT_ID
from .yoagent.transports import CodexAppServerSession
from .yoagent.transports import default_yoagent_transport_registry
from .yoagent.transports import normalize_yoagent_transport_id
from .yoagent.backends import YOAGENT_CLI_TIMEOUT_SECONDS
from .yoagent.backends import YOAGENT_STARTUP_QUESTION
from .yoagent.backends import codex_event_session_id
from .yoagent.backends import resolve_yoagent_backend
from .yoagent.backends import strip_yoagent_hidden_thinking
from .yoagent.backends import strip_yoagent_stream_hidden_thinking
from .yoagent.stream_events import ASSISTANT_DELTA
from .yoagent.stream_events import ERROR
from .yoagent.stream_events import HIDDEN_WORK_DELTA
from .yoagent.stream_events import HIDDEN_WORK_DONE
from .yoagent.stream_events import TOOL_CALL_DELTA
from .yoagent.stream_events import TOOL_CALL_FINISHED
from .yoagent.stream_events import TOOL_CALL_STARTED
from .yoagent.stream_events import TURN_DONE
from .yoagent.stream_events import normalize_yoagent_stream_event
from .yoagent.stream_events import yoagent_stream_event_auxiliary_line
from .yoagent.backends import yoagent_activity_payload_signature
from .yoagent.backends import yoagent_cli_auth_failure
from .yoagent.backends import yoagent_cli_fallback_reason
from .yoagent.backends import yoagent_language_directive
from .yoagent.backends import yoagent_response_details
from .yoagent.controller import YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS
from .yoagent.controller import YOAGENT_ACTION_AGENT_KINDS
from .yoagent.controller import YOAGENT_ACTION_PREVIEW_TTL_SECONDS
from .yoagent.controller import YOAGENT_ACTION_RESULT_MAX_CHARS
from .yoagent.controller import YOAGENT_ACTION_RESULT_POLL_SECONDS
from .yoagent.controller import YOAGENT_ACTION_RESULT_WAIT_SECONDS
from .yoagent.controller import YOAGENT_ACTION_TEXT_LIMIT
from .yoagent.controller import YOAGENT_JOB_DEFAULT_TIMEOUT_MINUTES
from .yoagent.controller import YOAGENT_JOB_IDLE_QUIET_SECONDS
from .yoagent.controller import YOAGENT_JOB_MAX_ITEMS
from .yoagent.controller import YOAGENT_JOB_POLL_SECONDS
from .yoagent.controller import YOAGENT_JOBS_STATE_KEY
from .yoagent.controller import YoagentController
from .yoagent.session_summaries import YOAGENT_SESSION_SUMMARIES_STATE_KEY
from .yoagent.session_summaries import YOAGENT_SESSION_SUMMARY_MAX_ITEMS
from .yoagent.session_summaries import YOAGENT_SESSION_SUMMARY_QUIET_SECONDS
from .yoagent.session_summaries import YOAGENT_SESSION_SUMMARY_STATES


logger = logging.getLogger(__name__)
ACTIVITY_SUMMARY_READY_PUSH_TRIGGERS = {"manual", "refresh", "force"}
METADATA_BADGE_PULSE_SECONDS = 20.0
METADATA_BADGES = ("main", "pr", "status", "ci")
METADATA_BADGE_SIGNATURES_STATE_KEY = "metadata_badge_signatures"
METADATA_BADGE_PULSE_UNTIL_STATE_KEY = "metadata_badge_pulse_until"
SESSION_FILES_CACHE_MAX_ITEMS = 64
SESSION_FILES_CACHE_SECONDS = 5.0
SESSION_FILES_CACHE_VERSION = 1
SESSION_FILES_CACHE_DIR = common.STATE_DIR / "session-files-cache"
SESSION_FILES_BATCH_MAX_WORKERS = 8
TRANSCRIPT_TAIL_CACHE_MAX_ITEMS = 128
TRANSCRIPTS_PAYLOAD_CACHE_SECONDS = 15.0
CONTEXT_ITEMS_CACHE_MAX_ITEMS = 128
SHARE_TOKEN_DEFAULT_TTL_SECONDS = 3600.0
SHARE_TOKEN_MAX_TTL_SECONDS = 8.0 * 3600.0
SHARE_MAX_VIEWERS_DEFAULT = 2
SHARE_MAX_VIEWERS_HARD_LIMIT = 300
SHARE_SHORT_ID_BYTES = 6
SHARE_DEBUG_PROFILE_EVENT_LIMIT = 100
SHARE_DEBUG_PROFILE_LOG_DIR = Path(os.environ.get("YOLOMUX_SHARE_DEBUG_DIR", "/tmp/yolomux-share-debug"))
SHARE_DEBUG_PROFILE_KEY_RE = re.compile(r"(token|secret|password|passwd|authorization|cookie|api[_-]?key|bearer)", re.I)
SHARE_DEBUG_PROFILE_URL_RE = re.compile(r"(?:https?://[^\"'\s<>]+)?/share/[A-Za-z0-9_-]+(?:#[^\"'\s<>]*)?")
SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS = 1.25
SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS = 1.009
TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS = 1.009
TMUX_SIGNAL_ACTIVITY_WINDOW_SECONDS = 120.0
SERVER_WATCHED_PR_EVENT_POLL_SECONDS = 60.0
SERVER_ACTIVITY_HEARTBEAT_ROTATE_SECONDS = 3600.0
CLIENT_WATCH_ROOT_TTL_SECONDS = 300
CLIENT_WATCH_ROOT_LIMIT = 128
CLIENT_WATCH_FILE_LIMIT = 128
DIRECTORY_WATCH_ENTRY_LIMIT = 512
# Keep in sync with tmuxSessionNameError() in static/yolomux.js.
TMUX_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,64}$")
DEFAULT_APP_SETTINGS = default_settings()
DEFAULT_PERFORMANCE_SETTINGS = DEFAULT_APP_SETTINGS["performance"]
SELF_RESTART_LOG_PATH = "/tmp/yolomux-self-update-restart.log"
SELF_RESTART_ENV_KEYS = (
    "PATH",
    "TERM",
    "PYTHONUNBUFFERED",
    "YOLOMUX_EXTRA_PATH",
    "YOLOMUX_CONFIG_DIR",
    "YOLOMUX_STATE_DIR",
    "YOLOMUX_TEST_AUTH_BYPASS",
    "VIRTUAL_ENV",
)


@dataclass(frozen=True)
class SelfRestartContext:
    root: str
    argv: list[str]
    env: dict[str, str]
    pid: int
    log_path: str = SELF_RESTART_LOG_PATH


def session_files_batch_worker_count(count: int) -> int:
    return max(1, min(SESSION_FILES_BATCH_MAX_WORKERS, count))


class SharedWatchRootIndex:
    """Cross-process directory watch-root index shared by every server using the same state dir."""

    def __init__(
        self,
        path: Path,
        owner_id: str,
        ttl_seconds: float = CLIENT_WATCH_ROOT_TTL_SECONDS,
        limit: int = CLIENT_WATCH_ROOT_LIMIT,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.path = Path(path)
        self.owner_id = str(owner_id)
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.limit = max(1, int(limit))
        self._clock = clock or (lambda: time.time())
        self._truncated_signature: tuple[Any, ...] | None = None

    def normalize_paths(self, roots: Any) -> list[str]:
        normalized: list[str] = []
        raw_roots = roots if isinstance(roots, list) else []
        for item in raw_roots:
            path = str(item or "").strip()
            if not path.startswith("/"):
                continue
            normalized.append(str(Path(path).expanduser()))
        unique = sorted(set(normalized))
        if len(unique) > self.limit:
            logger.warning("client watch roots truncated from %s to %s for owner %s", len(unique), self.limit, self.owner_id)
        return unique[: self.limit]

    def _empty_payload(self) -> dict[str, Any]:
        return {"version": 1, "owners": {}}

    def _read_payload(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return self._empty_payload()
        if not isinstance(raw, dict):
            return self._empty_payload()
        owners = raw.get("owners")
        if not isinstance(owners, dict):
            return self._empty_payload()
        return {"version": 1, "owners": owners}

    def _owner_entries(self, payload: dict[str, Any]) -> dict[str, Any]:
        owners = payload.get("owners")
        if not isinstance(owners, dict):
            owners = {}
            payload["owners"] = owners
        owner_payload = owners.get(self.owner_id)
        if not isinstance(owner_payload, dict):
            owner_payload = {}
            owners[self.owner_id] = owner_payload
        entries = owner_payload.get("entries")
        if not isinstance(entries, dict):
            entries = {}
            owner_payload["entries"] = entries
        owner_payload["updated_at"] = self._clock()
        return entries

    def _live_owner_entries(self, entries: dict[str, Any], now: float) -> dict[str, Any]:
        live: dict[str, Any] = {}
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "")
            expires_at = self._entry_expires_at(entry)
            if path.startswith("/") and expires_at > now:
                live[str(key)] = entry
        return live

    def _entry_expires_at(self, entry: dict[str, Any]) -> float:
        try:
            return float(entry.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _entry(self, path: str, source: str, expires_at: float, session: str = "") -> dict[str, Any]:
        item = {
            "path": path,
            "source": source,
            "expires_at": expires_at,
            "updated_at": self._clock(),
        }
        if session:
            item["session"] = session
        return item

    def _write_payload(self, payload: dict[str, Any]) -> None:
        atomic_write_text(self.path, json.dumps(payload, separators=(",", ":"), sort_keys=True), mode=0o600)

    def update_client_roots(self, roots: list[str]) -> None:
        now = self._clock()
        expires_at = now + self.ttl_seconds
        with file_lock(self.path):
            payload = self._read_payload()
            entries = {
                key: entry
                for key, entry in self._live_owner_entries(self._owner_entries(payload), now).items()
                if isinstance(entry, dict) and entry.get("source") != "client"
            }
            for path in roots[: self.limit]:
                entries[f"client:{path}"] = self._entry(path, "client", expires_at)
            self._owner_entries(payload).clear()
            self._owner_entries(payload).update(entries)
            self._write_payload(payload)

    def update_active_roots(self, roots_by_session: dict[str, str]) -> None:
        now = self._clock()
        expires_at = now + self.ttl_seconds
        with file_lock(self.path):
            payload = self._read_payload()
            entries = {
                key: entry
                for key, entry in self._live_owner_entries(self._owner_entries(payload), now).items()
                if isinstance(entry, dict) and entry.get("source") != "active"
            }
            for session, path in sorted(roots_by_session.items()):
                if not path.startswith("/"):
                    continue
                entries[f"active:{session}:{path}"] = self._entry(path, "active", expires_at, session=session)
            self._owner_entries(payload).clear()
            self._owner_entries(payload).update(entries)
            self._write_payload(payload)

    def snapshot(self) -> list[str]:
        now = self._clock()
        payload = self._read_payload()
        owners = payload.get("owners")
        if not isinstance(owners, dict):
            return []
        paths_by_owner: dict[str, set[str]] = {}
        for owner, owner_payload in owners.items():
            if not isinstance(owner_payload, dict):
                continue
            entries = owner_payload.get("entries")
            if not isinstance(entries, dict):
                continue
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path") or "")
                if not path.startswith("/") or self._entry_expires_at(entry) <= now:
                    continue
                paths_by_owner.setdefault(str(owner), set()).add(path)
        return self._limited_snapshot(paths_by_owner)

    def _limited_snapshot(self, paths_by_owner: dict[str, set[str]]) -> list[str]:
        total = sum(len(paths) for paths in paths_by_owner.values())
        owners = [(owner, sorted(paths)) for owner, paths in sorted(paths_by_owner.items()) if paths]
        if total <= self.limit:
            return sorted({path for _owner, paths in owners for path in paths})
        selected: list[str] = []
        seen: set[str] = set()
        index = 0
        while len(selected) < self.limit:
            added = False
            for _owner, paths in owners:
                if index >= len(paths):
                    continue
                path = paths[index]
                if path not in seen:
                    seen.add(path)
                    selected.append(path)
                    if len(selected) >= self.limit:
                        break
                added = True
            if not added:
                break
            index += 1
        signature = (total, self.limit, tuple((owner, len(paths)) for owner, paths in owners))
        if signature != self._truncated_signature:
            logger.warning("shared watch-root index truncated from %s live roots across %s owners to %s", total, len(owners), self.limit)
            self._truncated_signature = signature
        return sorted(selected)


def file_stat_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def filesystem_watch_signature(path: str | Path) -> tuple[Any, ...]:
    target = Path(path).expanduser()
    try:
        stat = target.lstat()
    except OSError:
        return (str(target), "missing")
    if not target.is_dir():
        return (str(target), "file", int(stat.st_mtime_ns), int(stat.st_size))
    entries: list[tuple[str, str, int, int]] = []
    try:
        children = sorted(target.iterdir(), key=lambda item: item.name)[:DIRECTORY_WATCH_ENTRY_LIMIT]
    except OSError:
        children = []
    for child in children:
        try:
            child_stat = child.lstat()
        except OSError:
            entries.append((child.name, "missing", 0, 0))
            continue
        kind = "dir" if child.is_dir() else "file"
        entries.append((child.name, kind, int(child_stat.st_mtime_ns), int(child_stat.st_size)))
    return (str(target), "dir", int(stat.st_mtime_ns), int(stat.st_size), tuple(entries))


def file_watch_signature(path: str | Path) -> tuple[Any, ...]:
    target = Path(path).expanduser()
    try:
        stat = target.lstat()
    except OSError:
        return (str(target), "missing")
    kind = "dir" if target.is_dir() else "file"
    return (str(target), kind, int(stat.st_mtime_ns), int(stat.st_size))


def filesystem_signature_entry_map(signature: tuple[Any, ...] | None) -> dict[str, tuple[str, int, int]]:
    if not isinstance(signature, tuple) or len(signature) < 5 or signature[1] != "dir":
        return {}
    entries = signature[4]
    if not isinstance(entries, tuple):
        return {}
    result: dict[str, tuple[str, int, int]] = {}
    for item in entries:
        if not isinstance(item, tuple) or len(item) < 4:
            continue
        result[str(item[0])] = (str(item[1]), int(item[2]), int(item[3]))
    return result


def filesystem_change_summary(previous: tuple[Any, ...] | None, current: tuple[Any, ...] | None) -> dict[str, Any]:
    def root_map(signature: tuple[Any, ...] | None) -> dict[str, tuple[Any, ...]]:
        result: dict[str, tuple[Any, ...]] = {}
        for item in signature or ():
            if not isinstance(item, tuple) or len(item) < 2 or not isinstance(item[1], tuple):
                continue
            result[str(item[0])] = item[1]
        return result

    previous_by_root = root_map(previous)
    current_by_root = root_map(current)
    summary: dict[str, Any] = {
        "roots_changed": 0,
        "roots_added": 0,
        "roots_removed": 0,
        "entries_added": 0,
        "entries_removed": 0,
        "entries_modified": 0,
        "files_added": 0,
        "files_removed": 0,
        "files_modified": 0,
        "dirs_added": 0,
        "dirs_removed": 0,
        "dirs_modified": 0,
        "roots": [],
    }
    for root in sorted(set(previous_by_root) | set(current_by_root)):
        prev_signature = previous_by_root.get(root)
        next_signature = current_by_root.get(root)
        if prev_signature == next_signature:
            continue
        summary["roots_changed"] += 1
        if prev_signature is None:
            summary["roots_added"] += 1
        if next_signature is None:
            summary["roots_removed"] += 1
        prev_entries = filesystem_signature_entry_map(prev_signature)
        next_entries = filesystem_signature_entry_map(next_signature)
        added = sorted(set(next_entries) - set(prev_entries))
        removed = sorted(set(prev_entries) - set(next_entries))
        modified = sorted(name for name in set(prev_entries) & set(next_entries) if prev_entries[name] != next_entries[name])
        root_summary = {
            "root": root,
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        }
        if len(summary["roots"]) < 12:
            summary["roots"].append(root_summary)
        summary["entries_added"] += len(added)
        summary["entries_removed"] += len(removed)
        summary["entries_modified"] += len(modified)
        for name in added:
            kind = next_entries[name][0]
            summary["dirs_added" if kind == "dir" else "files_added"] += 1
        for name in removed:
            kind = prev_entries[name][0]
            summary["dirs_removed" if kind == "dir" else "files_removed"] += 1
        for name in modified:
            kind = next_entries[name][0]
            summary["dirs_modified" if kind == "dir" else "files_modified"] += 1
    return summary


def agent_cache_signature(agent: AgentInfo) -> tuple[Any, ...]:
    if agent.transcript:
        try:
            transcript_signature = file_stat_signature(Path(agent.transcript))
        except OSError:
            transcript_signature = (str(agent.transcript), 0, 0)
    else:
        transcript_signature = ("", 0, 0)
    return (
        agent.kind or "",
        agent.cwd or "",
        agent.status or "",
        agent.session_id or "",
        agent.model or "",
        transcript_signature,
    )


def session_info_cache_signature(info: SessionInfo) -> tuple[Any, ...]:
    selected = info.selected_pane
    selected_signature = (
        selected.current_path,
        selected.command,
        selected.process_label or "",
        selected.pid,
    ) if selected else ("", "", "", 0)
    return (
        info.session,
        selected_signature,
        tuple(agent_cache_signature(agent) for agent in info.agents),
    )


def repo_refs_cache_signature(repo_refs: dict[str, dict[str, str]] | None) -> tuple[tuple[str, str, str], ...]:
    if not repo_refs:
        return ()
    rows: list[tuple[str, str, str]] = []
    for repo, refs in repo_refs.items():
        if not isinstance(refs, dict):
            continue
        rows.append((str(repo), str(refs.get("from") or ""), str(refs.get("to") or "")))
    return tuple(sorted(rows))


def resolve_yoagent_backend(backend: str) -> str:
    return yoagent_backends.resolve_yoagent_backend(backend, auth_status=agent_auth_status())


def tmux_session_name_error(name: str) -> str | None:
    if not name:
        return "session name is required"
    if len(name) > 64:
        return "session name must be 64 characters or fewer"
    if not TMUX_SESSION_NAME_RE.fullmatch(name):
        return "session name may contain only letters, numbers, spaces, dot, dash, and underscore"
    return None


def normalized_prompt_state(prompt: dict[str, Any] | None = None) -> dict[str, Any]:
    state = blank_prompt_state()
    if prompt:
        state.update(prompt)
    return state


def utc_iso_from_ts(value: Any) -> str:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        timestamp = 0.0
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp > 0 else ""


def compact_pull_request_for_history(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("number", "title", "url", "state", "draft", "source"):
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) and item not in ("", None):
            result[key] = truncate_text(item, 500) if isinstance(item, str) else item
    return result or None


def requires_known_session(refresh: bool = False) -> Callable[[Callable[..., tuple[Any, HTTPStatus]]], Callable[..., tuple[Any, HTTPStatus]]]:
    def decorator(func: Callable[..., tuple[Any, HTTPStatus]]) -> Callable[..., tuple[Any, HTTPStatus]]:
        @wraps(func)
        def wrapper(self: Any, session: str, *args: Any, **kwargs: Any) -> tuple[Any, HTTPStatus]:
            if refresh:
                self.refresh_sessions()
            unknown = self.require_known_session(session)
            if unknown:
                return unknown
            return func(self, session, *args, **kwargs)

        return wrapper

    return decorator


class YoagentAppDeps:
    def __init__(self, app: Any):
        object.__setattr__(self, "_app", app)

    def __getattr__(self, name: str) -> Any:
        try:
            return getattr(self._app, name)
        except AttributeError:
            if name in globals():
                return globals()[name]
            raise

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._app, name, value)

    def normalized_prompt_state(self, prompt: dict[str, Any] | None = None) -> dict[str, Any]:
        return normalized_prompt_state(prompt)

    def yoagent_cli_auth_failure(self, text: str) -> bool:
        return yoagent_cli_auth_failure(text)

    def strip_yoagent_hidden_thinking(self, text: str) -> tuple[str, bool]:
        return strip_yoagent_hidden_thinking(text)

    def strip_yoagent_stream_hidden_thinking(self, text: str) -> tuple[str, bool]:
        return strip_yoagent_stream_hidden_thinking(text)

    def yoagent_response_details(self, response: dict[str, Any]) -> str:
        return yoagent_response_details(response)

    def yoagent_cli_fallback_reason(self, backend: str, error: str) -> str:
        return yoagent_cli_fallback_reason(backend, error)

    def yoagent_language_directive(self, locale: str) -> str:
        return yoagent_language_directive(locale)

    def resolve_yoagent_backend(self, backend: str) -> str:
        return resolve_yoagent_backend(backend)

    def codex_event_session_id(self, event: dict[str, Any]) -> str:
        return codex_event_session_id(event)

    def yoagent_activity_payload_signature(self, activity_payload: dict[str, Any]) -> str:
        return yoagent_activity_payload_signature(activity_payload)


class TmuxWebtermApp:
    def __init__(self, sessions: list[str], dangerously_yolo: bool = False):
        self.sessions = sessions
        self.dangerously_yolo = dangerously_yolo
        self.auto_workers: dict[str, AutoApproveWorker] = {}
        self.auto_worker_sessions: dict[str, str] = {}
        self.auto_workers_lock = threading.RLock()
        self.share_tokens: dict[str, dict[str, Any]] = {}
        self.share_tokens_lock = threading.RLock()
        self.metadata_cache = MetadataCache()
        # DOIT.58 Phase 1: per-session/window user+agent activity ledger (heartbeat-coalesced
        # typed-time). Constructor defaults today; Preferences exposure is a deferred follow-up.
        self.activity_ledger = ActivityLedger(ACTIVITY_PATH, heartbeat_path=ACTIVITY_HEARTBEATS_PATH)
        self.activity_ledger.load()
        self.activity_heartbeat_next_rotate_at = 0.0
        self.session_files_cache_lock = threading.RLock()
        self.session_files_cache: dict[tuple[Any, ...], tuple[float, tuple[dict[str, Any], HTTPStatus]]] = {}
        self.session_files_refreshing_cache_keys: set[tuple[Any, ...]] = set()
        self.tabber_activity_cache_lock = threading.RLock()
        self.tabber_activity_cache: tuple[float, dict[str, Any]] | None = (time.monotonic(), self.build_activity_payload())
        self.tabber_activity_cache_refreshing = False
        self.tabber_activity_cache_warmer_thread: threading.Thread | None = None
        self.tabber_activity_cache_warmer_running = False
        self.tmux_signal_cache = TtlCache(TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS, max_entries=1)
        self.tmux_signal_event_watcher: TmuxSignalEventWatcher | None = None
        self.tmux_snapshot_history_lock = threading.RLock()
        self.tmux_snapshot_history_signatures: dict[tuple[str, str, int], tuple[int, int]] = {}
        # last-logged watched-PR truncation state, so the cap is logged only when it changes.
        self._watched_pr_truncated_signature: tuple[int, tuple[str, ...]] | None = None
        self.metadata_warm_lock = threading.Lock()
        self.metadata_warm_running = False
        self.metadata_badge_lock = threading.Lock()
        self.metadata_badge_signatures: dict[str, dict[str, str]] = {}
        self.metadata_badge_pulse_until: dict[str, dict[str, float]] = {}
        self.client_events = ClientEventBroker()
        self.client_watch_lock = threading.RLock()
        self.watch_root_owner_id = f"{SERVER_HOSTNAME}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self.watch_root_index = SharedWatchRootIndex(WATCH_INDEX_PATH, owner_id=self.watch_root_owner_id)
        self.client_watch_files: dict[str, float] = {}
        self.client_watch_background_files: dict[str, float] = {}
        self.client_watch_context_items: list[dict[str, Any]] = []
        self.client_watch_session_files: list[dict[str, Any]] = []
        self.client_watch_activity_summary: dict[str, Any] = {}
        self.client_watch_initialized = False
        self.client_watch_settings_signature: tuple[Any, ...] | None = None
        self.client_watch_transcripts_signature: tuple[Any, ...] | None = None
        self.client_watch_filesystem_signature: tuple[Any, ...] | None = None
        self.client_watch_file_signature: tuple[Any, ...] | None = None
        self.client_watch_background_file_signature: tuple[Any, ...] | None = None
        self.client_watch_auto_approve_signature: str = ""
        self.client_watch_tmux_signal_signature: str = ""
        self.client_watch_watched_prs_signature: str = ""
        self.client_watch_context_item_payload_signatures: dict[str, str] = {}
        self.client_watch_session_file_payload_signatures: dict[str, str] = {}
        self.client_watch_transcripts_payload_signature: str = ""
        self.client_watch_activity_summary_signature: str = ""
        self.client_watch_filesystem_payload_signature: str = ""
        self.client_watch_snapshot_running = False
        self.client_directory_poll_running = False
        self.client_watch_thread: threading.Thread | None = None
        self.client_watch_running = False
        self.client_watch_wake_event = threading.Event()
        self.client_event_next_signature_poll_at = 0.0
        self.client_event_next_file_poll_at = 0.0
        self.client_event_next_background_file_poll_at = 0.0
        self.client_event_next_auto_poll_at = 0.0
        self.client_event_next_tmux_signal_poll_at = 0.0
        self.client_event_next_watched_pr_poll_at = 0.0
        self.client_event_next_yoagent_job_poll_at = 0.0
        self.activity_summary_lock = threading.RLock()
        self.activity_summary_cache: dict[str, dict[str, Any]] = {}
        self.transcripts_payload_cache_lock = threading.RLock()
        self.transcripts_payload_cache: tuple[float, dict[str, Any]] | None = None
        self.transcripts_payload_refreshing = False
        self.transcript_tail_cache_lock = threading.RLock()
        self.transcript_tail_cache: dict[tuple[Any, ...], tuple[float, str]] = {}
        self.context_items_cache_lock = threading.RLock()
        self.context_items_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
        self.yoagent_cli_lock = threading.RLock()
        self.yoagent_cli_sessions: dict[str, dict[str, Any]] = yoagent_conversation.load_cli_sessions(monotonic_now=time.monotonic())
        self.yoagent_transports = default_yoagent_transport_registry()
        self.yoagent_controller = YoagentController(YoagentAppDeps(self))
        self.yoagent_managed_targets: dict[str, dict[str, Any]] = {}
        self.yoagent_stream_lock = threading.RLock()
        self.yoagent_stream_states: dict[str, dict[str, Any]] = {}
        self.yoagent_chat_request_lock = threading.RLock()
        self.yoagent_chat_requests: dict[str, dict[str, Any]] = {}
        self.yoagent_action_lock = threading.RLock()
        self.yoagent_action_previews: dict[str, dict[str, Any]] = {}
        self.yoagent_action_waits: dict[str, dict[str, Any]] = {}
        self.yoagent_job_lock = threading.RLock()
        self.yoagent_jobs: dict[str, dict[str, Any]] = self.load_yoagent_jobs()
        self.yoagent_prewarm_lock = threading.Lock()
        self.yoagent_prewarm_running = False
        self.yoagent_startup_response_running = False
        self.yoagent_prewarm_status: dict[str, Any] = {}
        self.yoagent_codex_app_server_lock = threading.RLock()
        self.yoagent_codex_app_server: CodexAppServerSession | None = None
        self.yoagent_codex_app_server_key = ""
        self.yoagent_session_summary_lock = threading.RLock()
        self.yoagent_session_summaries: dict[str, dict[str, Any]] = {}
        self.yoagent_summary_worker_lock = threading.Lock()
        self.yoagent_summary_worker_running = False
        self.yoagent_summary_first_launch_started = False
        self.load_metadata_badge_state()
        self.load_yoagent_session_summaries()
        self.event_log = EventLog(EVENT_LOG_PATH)
        self.run_history_store = RunHistoryStore(RUN_HISTORY_PATH)
        self.control_server = YolomuxControlServer(self.handle_control_request)
        self.control_server.start()

    def require_known_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus] | None:
        # The standard "unknown session -> 404" guard. Decorated handlers use requires_known_session();
        # payload-driven helpers and non-HTTP response shapes keep explicit checks.
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        return None

    def refresh_sessions(self) -> list[str]:
        sessions, error = list_tmux_session_names()
        if error is None:
            self.sessions = sessions
            self.prune_yoagent_session_summaries(set(sessions))
            self.activity_ledger.prune(set(sessions))
            self.rotate_activity_heartbeats_if_due()
            self.activity_ledger.flush()
            self.revoke_share_tokens_for_missing_sessions(set(sessions))
            return []
        return [error]

    def rotate_activity_heartbeats_if_due(self, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else float(now)
        if moment < self.activity_heartbeat_next_rotate_at:
            return 0
        kept = self.activity_ledger.rotate_heartbeats()
        self.activity_heartbeat_next_rotate_at = moment + SERVER_ACTIVITY_HEARTBEAT_ROTATE_SECONDS
        return kept

    def bounded_share_ttl_seconds(self, value: Any) -> float | None:
        if value is None or value == "":
            return SHARE_TOKEN_DEFAULT_TTL_SECONDS
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        return min(seconds, SHARE_TOKEN_MAX_TTL_SECONDS)

    def bounded_share_max_viewers(self, value: Any) -> int | None:
        if value is None or value == "":
            return SHARE_MAX_VIEWERS_DEFAULT
        try:
            viewers = int(value)
        except (TypeError, ValueError):
            return None
        if viewers <= 0:
            return None
        return min(viewers, SHARE_MAX_VIEWERS_HARD_LIMIT)

    def normalize_share_mode(self, value: Any = None, *, read_only: Any = None) -> str:
        if read_only is not None:
            if isinstance(read_only, bool):
                return "ro" if read_only else "rw"
            if str(read_only).strip().lower() in {"1", "true", "yes", "on", "readonly", "read-only", "ro"}:
                return "ro"
            return "rw"
        text = str(value or "").strip().lower()
        if text in {"rw", "write", "writable"}:
            return "rw"
        return "ro"

    def normalize_share_scheme(self, value: Any, *, base_url: str = "") -> str:
        text = str(value or "").strip().lower()
        if text in {"http", "https"}:
            return text
        try:
            scheme = urlsplit(str(base_url or "")).scheme.lower()
        except ValueError:
            scheme = ""
        return scheme if scheme in {"http", "https"} else "http"

    def share_base_url(self, base_url: str, scheme: str) -> str:
        root = str(base_url or "").rstrip("/")
        if not root:
            return ""
        try:
            parts = urlsplit(root)
        except ValueError:
            return root
        if not parts.netloc:
            return root
        return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    def share_record_sessions(self, record: dict[str, Any]) -> list[str]:
        raw_sessions = record.get("sessions")
        if isinstance(raw_sessions, list):
            sessions = [str(session or "").strip() for session in raw_sessions]
        else:
            sessions = [str(record.get("session") or "").strip()]
        result: list[str] = []
        for session in sessions:
            if session and session not in result:
                result.append(session)
        return result

    def share_record_primary_session(self, record: dict[str, Any]) -> str:
        sessions = self.share_record_sessions(record)
        return sessions[0] if sessions else ""

    def normalize_share_client_ip(self, value: Any) -> str:
        text = str(value or "").strip()
        return text[:80] if text else "unknown"

    def share_browser_label(self, name: str, version: str = "") -> str:
        clean_name = str(name or "").strip() or "Browser"
        clean_version = str(version or "").strip()
        if clean_version:
            clean_version = re.split(r"[^0-9A-Za-z._-]", clean_version, maxsplit=1)[0]
        return f"{clean_name} {clean_version}"[:80] if clean_version else clean_name[:80]

    def share_browser_version(self, user_agent: str, *patterns: str) -> str:
        for pattern in patterns:
            match = re.search(pattern, user_agent, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def normalize_share_browser_type(self, user_agent: Any) -> str:
        text = str(user_agent or "").strip()
        if not text:
            return "Unknown"
        lower = text.lower()
        if "edg/" in lower or "edge/" in lower:
            version = self.share_browser_version(
                text,
                r"\bEdgA?/([0-9][0-9A-Za-z._-]*)",
                r"\bEdge/([0-9][0-9A-Za-z._-]*)",
            )
            return self.share_browser_label("Edge", version)
        if "opr/" in lower or "opera" in lower:
            version = self.share_browser_version(
                text,
                r"\bOPR/([0-9][0-9A-Za-z._-]*)",
                r"\bOpera/([0-9][0-9A-Za-z._-]*)",
            )
            return self.share_browser_label("Opera", version)
        if "samsungbrowser/" in lower:
            version = self.share_browser_version(text, r"\bSamsungBrowser/([0-9][0-9A-Za-z._-]*)")
            return self.share_browser_label("Samsung Internet", version)
        if "firefox/" in lower or "fxios/" in lower:
            version = self.share_browser_version(
                text,
                r"\bFirefox/([0-9][0-9A-Za-z._-]*)",
                r"\bFxiOS/([0-9][0-9A-Za-z._-]*)",
            )
            return self.share_browser_label("Firefox", version)
        if "crios/" in lower or "chrome/" in lower or "chromium/" in lower:
            version = self.share_browser_version(
                text,
                r"\bCriOS/([0-9][0-9A-Za-z._-]*)",
                r"\bChrome/([0-9][0-9A-Za-z._-]*)",
                r"\bChromium/([0-9][0-9A-Za-z._-]*)",
            )
            return self.share_browser_label("Chrome", version)
        if "safari/" in lower:
            version = self.share_browser_version(text, r"\bVersion/([0-9][0-9A-Za-z._-]*)")
            return self.share_browser_label("Safari", version)
        if "msie " in lower or "trident/" in lower:
            return "Internet Explorer"
        if "curl/" in lower:
            return self.share_browser_label("curl", self.share_browser_version(text, r"\bcurl/([0-9][0-9A-Za-z._-]*)"))
        if "python-requests/" in lower:
            version = self.share_browser_version(text, r"\bpython-requests/([0-9][0-9A-Za-z._-]*)")
            return self.share_browser_label("Python", version)
        return "Browser"

    def normalize_share_viewer_record(self, value: Any, now: float | None = None) -> dict[str, Any] | None:
        current_time = time.time() if now is None else now
        if isinstance(value, dict):
            raw_count = value.get("count", value.get("refcount", 0))
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                count = 0
            try:
                connected_at = float(value.get("connected_at", value.get("connectedAt", current_time)) or current_time)
            except (TypeError, ValueError):
                connected_at = current_time
            try:
                last_seen_at = float(value.get("last_seen_at", value.get("lastSeenAt", connected_at)) or connected_at)
            except (TypeError, ValueError):
                last_seen_at = connected_at
            ip = self.normalize_share_client_ip(value.get("ip", ""))
            browser = str(value.get("browser") or "").strip()[:80] or self.normalize_share_browser_type(value.get("user_agent", value.get("userAgent", "")))
        else:
            try:
                count = int(value)
            except (TypeError, ValueError):
                count = 0
            connected_at = current_time
            last_seen_at = current_time
            ip = "unknown"
            browser = "Unknown"
        if count <= 0:
            return None
        return {
            "count": count,
            "connected_at": connected_at,
            "last_seen_at": last_seen_at,
            "ip": ip,
            "browser": browser,
        }

    def share_record_viewer_ids(self, record: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_viewers = record.get("viewer_ids")
        if not isinstance(raw_viewers, dict):
            raw_viewers = {}
            record["viewer_ids"] = raw_viewers
        normalized: dict[str, dict[str, Any]] = {}
        now = time.time()
        for viewer_id, value in raw_viewers.items():
            clean_id = str(viewer_id or "").strip()
            entry = self.normalize_share_viewer_record(value, now)
            if clean_id and entry:
                normalized[clean_id] = entry
        record["viewer_ids"] = normalized
        return record["viewer_ids"]

    def share_record_viewer_count(self, record: dict[str, Any]) -> int:
        return len(self.share_record_viewer_ids(record))

    def share_record_viewer_details(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        now = time.time()
        items = sorted(
            self.share_record_viewer_ids(record).items(),
            key=lambda item: (float(item[1].get("connected_at") or 0.0), str(item[0] or "")),
        )
        details: list[dict[str, Any]] = []
        for _viewer_id, entry in items:
            connected_at = float(entry.get("connected_at") or now)
            details.append({
                "connected_at": connected_at,
                "connected_seconds": max(0.0, now - connected_at),
                "last_seen_at": float(entry.get("last_seen_at") or connected_at),
                "ip": self.normalize_share_client_ip(entry.get("ip", "")),
                "browser": str(entry.get("browser") or "Unknown")[:80],
            })
        return details

    def share_record_snapshot(self, record: dict[str, Any], token: str = "") -> dict[str, Any]:
        result = copy.deepcopy(record)
        result["viewer_ids"] = {viewer_id: dict(entry) for viewer_id, entry in self.share_record_viewer_ids(record).items()}
        if token:
            result["token"] = token
        return result

    def normalize_share_finder_state(self, value: Any, share_sessions: list[str]) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        root = str(value.get("root") or "").strip()
        root_mode = str(value.get("rootMode") or value.get("root_mode") or "").strip()
        mode = str(value.get("mode") or "").strip()
        session = str(value.get("session") or "").strip()
        result: dict[str, str] = {}
        if root:
            result["root"] = root
        if root_mode in {"fixed", "sync"}:
            result["rootMode"] = root_mode
        if mode in {"files", "diff", "tabber"}:
            result["mode"] = mode
        if session and session in share_sessions:
            result["session"] = session
        return result

    def normalize_share_ui_state(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        try:
            normalized = json.loads(json.dumps(value)) if value else {}
        except (TypeError, ValueError):
            return {}
        viewport = self.normalize_share_viewport_state(normalized.get("viewport"))
        if viewport:
            normalized["viewport"] = viewport
        else:
            normalized.pop("viewport", None)
        appearance = self.normalize_share_appearance_state(normalized.get("appearance"))
        if appearance:
            normalized["appearance"] = appearance
        else:
            normalized.pop("appearance", None)
        scroll = self.normalize_share_scroll_state(normalized.get("scroll"))
        if scroll:
            normalized["scroll"] = scroll
        else:
            normalized.pop("scroll", None)
        return normalized

    def normalize_share_scroll_state(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        def clean_scroll_number(raw: Any) -> int:
            try:
                return max(0, int(round(float(raw or 0))))
            except (TypeError, ValueError, OverflowError):
                return 0

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value[:100]:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or "").strip()
            if not target or target in seen:
                continue
            seen.add(target)
            entry: dict[str, Any] = {
                "target": target,
                "kind": str(item.get("kind") or "").strip()[:32],
                "top": clean_scroll_number(item.get("top")),
                "left": clean_scroll_number(item.get("left")),
            }
            for key in ("path", "item", "source", "mode", "session"):
                value_text = str(item.get(key) or "").strip()
                if value_text:
                    entry[key] = value_text[:2048]
            for key in ("anchor", "head"):
                if key not in item:
                    continue
                entry[key] = clean_scroll_number(item.get(key))
            result.append(entry)
        return result

    def normalize_share_viewport_state(self, value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        try:
            width = int(round(float(value.get("width", value.get("w", 0)))))
            height = int(round(float(value.get("height", value.get("h", 0)))))
        except (TypeError, ValueError, OverflowError):
            return {}
        if width <= 0 or height <= 0:
            return {}
        return {
            "width": max(1, min(100_000, width)),
            "height": max(1, min(100_000, height)),
        }

    def normalize_share_appearance_state(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        number_fields: dict[str, tuple[float, float]] = {
            "uiFontSize": (6, 20),
            "terminalFontSize": (6, 28),
            "terminalLineHeight": (0.8, 2.0),
            "editorFontSize": (6, 28),
            "previewFontSize": (6, 32),
            "fileExplorerFontSize": (6, 24),
            "tabWidth": (120, 420),
            "paneSpacing": (0, 20),
            "paneRingOpacity": (5, 100),
            "inactivePaneOpacity": (0, 100),
        }
        string_fields: dict[str, set[str]] = {
            "locale": {"en", "zh-Hant", "zh-Hans", "ja", "ko", "es", "de", "fr", "it", "pt-BR", "pl", "nl", "he", "ar", "ru", "hi", "vi", "th", "tr", "en-XA"},
            "languagePref": {"system", "en", "zh-Hant", "zh-Hans", "ja", "ko", "es", "de", "fr", "it", "pt-BR", "pl", "nl", "he", "ar", "ru", "hi", "vi", "th", "tr", "en-XA"},
            "theme": {"dark", "light", "system"},
            "resolvedTheme": {"dark", "light"},
            "terminalTheme": {"dark", "light", "follow-app"},
            "activeColor": {"green", "blue", "orange", "yellow", "purple", "white"},
            "separatorColor": {"theme", "green", "blue", "orange", "yellow", "purple", "white"},
        }
        result: dict[str, Any] = {}
        for key, (low, high) in number_fields.items():
            if key not in value:
                continue
            try:
                number = float(value.get(key))
            except (TypeError, ValueError, OverflowError):
                continue
            if not (number == number):
                continue
            result[key] = max(low, min(high, number))
        for key, allowed in string_fields.items():
            text = str(value.get(key) or "").strip()
            if text in allowed:
                result[key] = text
        return result

    def share_record_layout_file_paths(self, record: dict[str, Any]) -> set[str]:
        paths: set[str] = set()
        prefixes = ("file:", "filediff:", "image:")
        for part in str(record.get("tabs") or "").split(";"):
            _slot, separator, raw_items = part.partition(":")
            if not separator:
                continue
            for raw_item in raw_items.split(","):
                token = raw_item.strip()
                if token.endswith("*"):
                    token = token[:-1]
                item = unquote(token)
                if item.startswith("filecopy:"):
                    _copy_id, copy_separator, path = item.removeprefix("filecopy:").partition(":")
                    if copy_separator and path.startswith("/"):
                        paths.add(path)
                    continue
                for prefix in prefixes:
                    if item.startswith(prefix):
                        path = item[len(prefix):]
                        if path.startswith("/"):
                            paths.add(path)
        ui_state = record.get("ui_state")
        editor = ui_state.get("editor") if isinstance(ui_state, dict) else None
        modes = editor.get("modes") if isinstance(editor, dict) else None
        if isinstance(modes, list):
            for entry in modes:
                path = str(entry.get("path") or "") if isinstance(entry, dict) else ""
                if path.startswith("/"):
                    paths.add(path)
        return paths

    def share_record_layout_sessions(self, tabs: Any) -> list[str]:
        active_sessions = set(self.sessions)
        sessions: list[str] = []
        for part in str(tabs or "").split(";"):
            _slot, separator, raw_items = part.partition(":")
            if not separator:
                continue
            for raw_item in raw_items.split(","):
                token = raw_item.strip()
                if token.endswith("*"):
                    token = token[:-1]
                item = unquote(token)
                if item in active_sessions and item not in sessions:
                    sessions.append(item)
        return sessions

    def share_record_allows_file_path(self, record: dict[str, Any] | None, raw_path: str) -> bool:
        if not record:
            return False
        path = str(raw_path or "").strip()
        return bool(path) and path in self.share_record_layout_file_paths(record)

    def normalize_share_viewer_id(self, value: Any) -> str:
        text = str(value or "").strip()
        return text[:128] if text else "legacy"

    def share_record_sessions_are_active(self, record: dict[str, Any], active_sessions: set[str] | None = None) -> bool:
        sessions = self.share_record_sessions(record)
        if not sessions:
            return False
        current = set(self.sessions) if active_sessions is None else active_sessions
        return all(session in current for session in sessions)

    def normalize_share_session_list(self, session: Any, sessions: Any = None) -> tuple[list[str], str]:
        raw_values: list[Any] = []
        if isinstance(sessions, list):
            raw_values.extend(sessions)
        elif isinstance(sessions, tuple):
            raw_values.extend(sessions)
        elif isinstance(sessions, str):
            raw_values.extend(sessions.split(","))
        raw_values.append(session)
        result: list[str] = []
        for raw in raw_values:
            value = str(raw or "").strip()
            if not value or value in result:
                continue
            if value not in self.sessions:
                return [], value
            result.append(value)
        return result, ""

    def prune_inactive_share_tokens_locked(self, now: float) -> None:
        for token, record in list(self.share_tokens.items()):
            if float(record.get("expires_at") or 0.0) <= now:
                self.share_tokens.pop(token, None)
                continue
            if not self.share_record_sessions_are_active(record):
                record["revoked"] = True

    def active_share_records_locked(self, now: float) -> list[dict[str, Any]]:
        self.prune_inactive_share_tokens_locked(now)
        records: list[dict[str, Any]] = []
        for record in self.share_tokens.values():
            if not record.get("revoked") and self.share_record_sessions_are_active(record) and float(record.get("expires_at") or 0.0) > now:
                records.append(record)
        return records

    def active_share_entries_locked(self, now: float) -> list[tuple[str, dict[str, Any]]]:
        self.prune_inactive_share_tokens_locked(now)
        entries: list[tuple[str, dict[str, Any]]] = []
        for token, record in self.share_tokens.items():
            if not record.get("revoked") and self.share_record_sessions_are_active(record) and float(record.get("expires_at") or 0.0) > now:
                entries.append((token, record))
        return entries

    def new_share_short_id_locked(self) -> str:
        existing = {str(record.get("short_id") or "") for record in self.share_tokens.values()}
        while True:
            short_id = secrets.token_urlsafe(SHARE_SHORT_ID_BYTES).rstrip("=")
            if short_id and short_id not in existing:
                return short_id

    def share_url_for_record(self, token: str, record: dict[str, Any], base_url: str = "") -> str:
        root = self.share_base_url(base_url, str(record.get("scheme") or "http"))
        short_id = str(record.get("short_id") or "")
        path = f"/share/{short_id}"
        return f"{root}{path}#t={token}" if root else f"{path}#t={token}"

    def share_status_frame_for_record(self, record: dict[str, Any]) -> dict[str, Any]:
        ui_state = record.get("ui_state") if isinstance(record.get("ui_state"), dict) else {}
        return {
            "active": not bool(record.get("revoked")),
            "session": self.share_record_primary_session(record),
            "sessions": self.share_record_sessions(record),
            "created_at": float(record.get("created_at") or 0.0),
            "created_by": str(record.get("created_by") or ""),
            "expires_at": float(record.get("expires_at") or 0.0),
            "ttl_seconds": max(0.0, float(record.get("expires_at") or 0.0) - time.time()),
            "mode": str(record.get("mode") or "ro"),
            "scheme": str(record.get("scheme") or "http"),
            "short_id": str(record.get("short_id") or ""),
            "max_viewers": int(record.get("max_viewers") or 0),
            "viewers": self.share_record_viewer_count(record),
            "viewer_details": self.share_record_viewer_details(record),
            "debug_profile": bool(record.get("debug_profile")),
            "viewport": ui_state.get("viewport") if isinstance(ui_state.get("viewport"), dict) else {},
        }

    def share_status_frame_payload(self, token: str) -> dict[str, Any] | None:
        record = self.verify_share_token(token)
        if record is None:
            return None
        return self.share_status_frame_for_record(record)

    def share_payload_for_record(self, token: str, record: dict[str, Any], base_url: str = "") -> dict[str, Any]:
        ui_state = record.get("ui_state") if isinstance(record.get("ui_state"), dict) else {}
        debug_profile = bool(record.get("debug_profile"))
        return {
            "ok": True,
            "active": True,
            "token": token,
            "url": self.share_url_for_record(token, record, base_url),
            "session": self.share_record_primary_session(record),
            "sessions": self.share_record_sessions(record),
            "created_at": float(record.get("created_at") or 0.0),
            "created_by": str(record.get("created_by") or ""),
            "expires_at": float(record.get("expires_at") or 0.0),
            "ttl_seconds": max(0.0, float(record.get("expires_at") or 0.0) - time.time()),
            "mode": str(record.get("mode") or "ro"),
            "scheme": str(record.get("scheme") or "http"),
            "short_id": str(record.get("short_id") or ""),
            "max_viewers": int(record.get("max_viewers") or 0),
            "viewers": self.share_record_viewer_count(record),
            "viewer_details": self.share_record_viewer_details(record),
            "http_allowed": bool(record.get("http_allowed")),
            "debug_profile": debug_profile,
            "debugProfile": debug_profile,
            "finder": record.get("finder") if isinstance(record.get("finder"), dict) else {},
            "layout": str(record.get("layout") or ""),
            "tabs": str(record.get("tabs") or ""),
            "viewport": ui_state.get("viewport") if isinstance(ui_state.get("viewport"), dict) else {},
            "appearance": ui_state.get("appearance") if isinstance(ui_state.get("appearance"), dict) else {},
            "uiState": ui_state,
        }

    def create_share_token(
        self,
        session: str,
        ttl_seconds: Any = SHARE_TOKEN_DEFAULT_TTL_SECONDS,
        *,
        base_url: str = "",
        created_by: str = "",
        layout: str = "",
        tabs: str = "",
        finder: Any = None,
        ui_state: Any = None,
        sessions: Any = None,
        mode: Any = None,
        read_only: Any = None,
        scheme: Any = None,
        max_viewers: Any = None,
        debug_profile: Any = False,
        request_is_https: bool = False,
        tls_available: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        share_sessions, bad_session = self.normalize_share_session_list(session, sessions)
        if bad_session:
            return {"session": bad_session, "error": f"unknown session: {bad_session}"}, HTTPStatus.NOT_FOUND
        if not share_sessions:
            return {"session": "", "error": "at least one tmux session is required"}, HTTPStatus.BAD_REQUEST
        primary_session = share_sessions[0]
        bounded_ttl = self.bounded_share_ttl_seconds(ttl_seconds)
        if bounded_ttl is None:
            return {"session": primary_session, "sessions": share_sessions, "error": "ttl must be a positive number of seconds"}, HTTPStatus.BAD_REQUEST
        bounded_viewers = self.bounded_share_max_viewers(max_viewers)
        if bounded_viewers is None:
            return {"session": primary_session, "sessions": share_sessions, "error": "max_viewers must be a positive integer"}, HTTPStatus.BAD_REQUEST
        requested_mode = self.normalize_share_mode(mode, read_only=read_only)
        requested_scheme = self.normalize_share_scheme(scheme, base_url=base_url)
        if not tls_available:
            share_mode = "ro"
            share_scheme = "http"
        elif requested_mode == "rw":
            if requested_scheme != "https" or not request_is_https:
                return {"session": primary_session, "sessions": share_sessions, "error": "write shares require https"}, HTTPStatus.BAD_REQUEST
            share_mode = "rw"
            share_scheme = "https"
        else:
            share_mode = "ro"
            share_scheme = requested_scheme
        now = time.time()
        expires_at = now + bounded_ttl
        token = secrets.token_urlsafe(32)
        finder_state = self.normalize_share_finder_state(finder, share_sessions)
        normalized_ui_state = self.normalize_share_ui_state(ui_state)
        with self.share_tokens_lock:
            short_id = self.new_share_short_id_locked()
            record = {
                "session": primary_session,
                "sessions": share_sessions,
                "created_at": now,
                "expires_at": expires_at,
                "created_by": str(created_by or ""),
                "revoked": False,
                "layout": str(layout or ""),
                "tabs": str(tabs or ""),
                "finder": finder_state,
                "ui_state": normalized_ui_state,
                "mode": share_mode,
                "max_viewers": bounded_viewers,
                "viewers": 0,
                "viewer_ids": {},
                "scheme": share_scheme,
                "short_id": short_id,
                "http_allowed": share_scheme == "http" and share_mode == "ro",
                "debug_profile": bool(debug_profile),
                "debug_profile_events": [],
            }
            self.share_tokens[token] = record
        return self.share_payload_for_record(token, record, base_url) | {"ttl_seconds": bounded_ttl}, HTTPStatus.OK

    def update_share_record_ui_state(self, token: str, payload: dict[str, Any]) -> None:
        clean_token = str(token or "")
        if not clean_token or not isinstance(payload, dict):
            return
        with self.share_tokens_lock:
            record = self.share_tokens.get(clean_token)
            if not record or record.get("revoked"):
                return
            sessions = self.share_record_sessions(record)
            if "layout" in payload:
                record["layout"] = str(payload.get("layout") or "")
            layout_sessions: list[str] = []
            if "tabs" in payload:
                record["tabs"] = str(payload.get("tabs") or "")
                layout_sessions = self.share_record_layout_sessions(record["tabs"])
            finder = payload.get("finder")
            if isinstance(finder, dict):
                finder_session = str(finder.get("session") or "").strip()
                if layout_sessions and finder_session in self.sessions and finder_session not in layout_sessions:
                    layout_sessions.append(finder_session)
                record["finder"] = self.normalize_share_finder_state(finder, layout_sessions or sessions)
            if layout_sessions:
                record["sessions"] = layout_sessions
                record["session"] = layout_sessions[0]
            ui_state = payload.get("uiState", payload.get("ui_state", None))
            if isinstance(ui_state, dict):
                record["ui_state"] = self.normalize_share_ui_state(ui_state)
            ui_state_patch = payload.get("uiStatePatch", payload.get("ui_state_patch", None))
            if isinstance(ui_state_patch, dict):
                current_ui_state = record.get("ui_state") if isinstance(record.get("ui_state"), dict) else {}
                record["ui_state"] = self.normalize_share_ui_state({**current_ui_state, **ui_state_patch})
            ui_state_scroll = payload.get("uiStateScroll", payload.get("ui_state_scroll", None))
            if isinstance(ui_state_scroll, dict):
                current_ui_state = record.get("ui_state") if isinstance(record.get("ui_state"), dict) else {}
                current_scroll = self.normalize_share_scroll_state(current_ui_state.get("scroll"))
                next_scroll = self.normalize_share_scroll_state([ui_state_scroll])
                if next_scroll:
                    by_target = {entry["target"]: entry for entry in current_scroll}
                    by_target[next_scroll[0]["target"]] = next_scroll[0]
                    record["ui_state"] = self.normalize_share_ui_state({
                        **current_ui_state,
                        "scroll": list(by_target.values()),
                    })

    def active_share_payload(self, *, base_url: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        now = time.time()
        with self.share_tokens_lock:
            entries = self.active_share_entries_locked(now)
            if not entries:
                return {"ok": True, "active": False, "shares": []}, HTTPStatus.OK
            shares = [self.share_payload_for_record(token, record, base_url) for token, record in entries]
            token, record = entries[0]
            return self.share_payload_for_record(token, record, base_url) | {"shares": shares}, HTTPStatus.OK

    def share_status_payload(self, token: str, *, base_url: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        record = self.verify_share_token(token)
        if record is None:
            return {"ok": False, "active": False, "error": "share token expired or revoked"}, HTTPStatus.UNAUTHORIZED
        clean_token = str(record.get("token") or token or "")
        return self.share_payload_for_record(clean_token, record, base_url) | {"shares": []}, HTTPStatus.OK

    def redact_share_debug_profile_value(self, value: Any, key: str = "", depth: int = 0) -> Any:
        if depth > 12:
            return "[truncated-depth]"
        if SHARE_DEBUG_PROFILE_KEY_RE.search(str(key or "")):
            return "[redacted-share-token]"
        if isinstance(value, dict):
            return {str(name)[:120]: self.redact_share_debug_profile_value(item, str(name), depth + 1) for name, item in value.items()}
        if isinstance(value, list):
            return [self.redact_share_debug_profile_value(item, key, depth + 1) for item in value[:256]]
        if isinstance(value, str):
            text = SHARE_DEBUG_PROFILE_URL_RE.sub("[redacted-share-url]", value)
            text = re.sub(r"([?#&](?:t|token|share|shareToken|share_token)=)[^&#\s\"']+", r"\1[redacted-share-token]", text, flags=re.I)
            text = re.sub(r"\b((?:token|shareToken|share_token)=)[^&#\s\"']+", r"\1[redacted-share-token]", text, flags=re.I)
            return text[:4000] + ("[truncated]" if len(text) > 4000 else "")
        return value

    def append_share_debug_profile_event(self, event: dict[str, Any]) -> tuple[bool, str]:
        short_id = str(event.get("share_id") or "unknown")
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", short_id).strip(".")[:80] or "unknown"
        try:
            SHARE_DEBUG_PROFILE_LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = SHARE_DEBUG_PROFILE_LOG_DIR / f"{safe_id}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str) + "\n")
            return True, str(path)
        except OSError as exc:
            return False, str(exc)[:240]

    def record_share_debug_profile(self, token: str, payload: dict[str, Any], *, ip: str = "", user_agent: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        raw_token = str(token or "")
        if not raw_token:
            return {"ok": False, "error": "share token required"}, HTTPStatus.UNAUTHORIZED
        if not isinstance(payload, dict):
            return {"ok": False, "error": "debug profile payload must be an object"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        clean_payload = self.redact_share_debug_profile_value(payload)
        event: dict[str, Any] | None = None
        with self.share_tokens_lock:
            self.prune_inactive_share_tokens_locked(now)
            for stored_token, record in self.share_tokens.items():
                if not hmac.compare_digest(stored_token, raw_token):
                    continue
                if record.get("revoked") or not self.share_record_sessions_are_active(record):
                    return {"ok": False, "error": "share token expired or revoked"}, HTTPStatus.UNAUTHORIZED
                if not bool(record.get("debug_profile")):
                    return {"ok": False, "error": "debug/profiling upload is not enabled for this share"}, HTTPStatus.FORBIDDEN
                event = {
                    "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "share_id": str(record.get("short_id") or ""),
                    "kind": str(clean_payload.get("kind") or "share-debug-profile")[:120],
                    "viewer_id": str(clean_payload.get("viewerId") or clean_payload.get("viewer_id") or "")[:120],
                    "client_ip": self.normalize_share_client_ip(ip),
                    "browser": self.normalize_share_browser_type(user_agent),
                    "payload": clean_payload,
                }
                events = record.setdefault("debug_profile_events", [])
                if not isinstance(events, list):
                    events = []
                    record["debug_profile_events"] = events
                events.append(event)
                del events[:-SHARE_DEBUG_PROFILE_EVENT_LIMIT]
                break
        if event is None:
            return {"ok": False, "error": "share token expired or revoked"}, HTTPStatus.UNAUTHORIZED
        logged, log_detail = self.append_share_debug_profile_event(event)
        result = {
            "ok": True,
            "stored": True,
            "logged": logged,
            "events": 1,
        }
        if not logged:
            result["log_error"] = log_detail
        return result, HTTPStatus.OK

    def extend_share_token(self, token_or_short_id: str, add_seconds: Any = 600, *, base_url: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        target = str(token_or_short_id or "").strip()
        if not target:
            return {"ok": False, "error": "share token or id required"}, HTTPStatus.BAD_REQUEST
        bounded_add = self.bounded_share_ttl_seconds(add_seconds)
        if bounded_add is None:
            return {"ok": False, "error": "extension must be a positive number of seconds"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        with self.share_tokens_lock:
            for token, record in self.active_share_entries_locked(now):
                if not (
                    hmac.compare_digest(token, target)
                    or hmac.compare_digest(str(record.get("short_id") or ""), target)
                ):
                    continue
                current_expires_at = max(now, float(record.get("expires_at") or 0.0))
                max_expires_at = now + SHARE_TOKEN_MAX_TTL_SECONDS
                record["expires_at"] = min(max_expires_at, current_expires_at + bounded_add)
                return self.share_payload_for_record(token, record, base_url) | {"extended": True}, HTTPStatus.OK
        return {"ok": False, "error": "share token expired or revoked"}, HTTPStatus.NOT_FOUND

    def http_allowed_share_is_active(self) -> bool:
        now = time.time()
        with self.share_tokens_lock:
            return any(bool(record.get("http_allowed")) for _token, record in self.active_share_entries_locked(now))

    def stop_active_share(self, token_or_short_id: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        now = time.time()
        stopped = 0
        target = str(token_or_short_id or "").strip()
        with self.share_tokens_lock:
            for token, record in self.active_share_entries_locked(now):
                if target and not (
                    hmac.compare_digest(token, target)
                    or hmac.compare_digest(str(record.get("short_id") or ""), target)
                ):
                    continue
                if not record.get("revoked"):
                    record["revoked"] = True
                    stopped += 1
        with self.share_tokens_lock:
            remaining = len(self.active_share_entries_locked(time.time()))
        return {"ok": True, "active": remaining > 0, "stopped": stopped}, HTTPStatus.OK

    def verify_share_token(self, token: str) -> dict[str, Any] | None:
        raw_token = str(token or "")
        if not raw_token:
            return None
        now = time.time()
        with self.share_tokens_lock:
            for stored_token, record in list(self.share_tokens.items()):
                expired = float(record.get("expires_at") or 0.0) <= now
                if expired:
                    self.share_tokens.pop(stored_token, None)
                    continue
                if not self.share_record_sessions_are_active(record):
                    record["revoked"] = True
                if not hmac.compare_digest(stored_token, raw_token):
                    continue
                if record.get("revoked") or not self.share_record_sessions_are_active(record):
                    return None
                return self.share_record_snapshot(record, stored_token)
        return None

    def register_share_viewer(self, token: str, session: str = "", viewer_id: str = "", ip: str = "", user_agent: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        raw_token = str(token or "")
        if not raw_token:
            return {"error": "share token required"}, HTTPStatus.UNAUTHORIZED
        requested_session = str(session or "").strip()
        clean_viewer_id = self.normalize_share_viewer_id(viewer_id)
        now = time.time()
        with self.share_tokens_lock:
            for stored_token, record in list(self.share_tokens.items()):
                expired = float(record.get("expires_at") or 0.0) <= now
                if expired:
                    self.share_tokens.pop(stored_token, None)
                    continue
                if not self.share_record_sessions_are_active(record):
                    record["revoked"] = True
                if not hmac.compare_digest(stored_token, raw_token):
                    continue
                share_sessions = self.share_record_sessions(record)
                if requested_session and requested_session not in share_sessions:
                    return {"error": "share token is scoped to a different session"}, HTTPStatus.FORBIDDEN
                if record.get("revoked") or not self.share_record_sessions_are_active(record):
                    return {"error": "share token expired or revoked"}, HTTPStatus.UNAUTHORIZED
                viewer_ids = self.share_record_viewer_ids(record)
                viewers = len(viewer_ids)
                max_viewers = int(record.get("max_viewers") or 0)
                if clean_viewer_id not in viewer_ids and max_viewers > 0 and viewers >= max_viewers:
                    return {"error": "share viewer limit reached"}, HTTPStatus.FORBIDDEN
                current = viewer_ids.get(clean_viewer_id)
                if not current:
                    current = {
                        "count": 0,
                        "connected_at": now,
                        "last_seen_at": now,
                        "ip": self.normalize_share_client_ip(ip),
                        "browser": self.normalize_share_browser_type(user_agent),
                    }
                current["count"] = max(0, int(current.get("count") or 0)) + 1
                current["last_seen_at"] = now
                clean_ip = self.normalize_share_client_ip(ip)
                if clean_ip != "unknown":
                    current["ip"] = clean_ip
                browser = self.normalize_share_browser_type(user_agent)
                if browser != "Unknown":
                    current["browser"] = browser
                viewer_ids[clean_viewer_id] = current
                record["viewers"] = len(viewer_ids)
                result = self.share_record_snapshot(record, stored_token)
                result["viewer_id"] = clean_viewer_id
                result["viewers"] = record["viewers"]
                result["viewer_details"] = self.share_record_viewer_details(record)
                return result, HTTPStatus.OK
        return {"error": "share token expired or revoked"}, HTTPStatus.UNAUTHORIZED

    def unregister_share_viewer(self, token: str, viewer_id: str = "") -> int:
        raw_token = str(token or "")
        if not raw_token:
            return 0
        clean_viewer_id = self.normalize_share_viewer_id(viewer_id)
        with self.share_tokens_lock:
            for stored_token, record in self.share_tokens.items():
                if not hmac.compare_digest(stored_token, raw_token):
                    continue
                viewer_ids = self.share_record_viewer_ids(record)
                current = viewer_ids.get(clean_viewer_id) or {}
                remaining_for_viewer = max(0, int(current.get("count") or 0) - 1)
                if remaining_for_viewer:
                    current["count"] = remaining_for_viewer
                    current["last_seen_at"] = time.time()
                    viewer_ids[clean_viewer_id] = current
                else:
                    viewer_ids.pop(clean_viewer_id, None)
                record["viewers"] = len(viewer_ids)
                return record["viewers"]
        return 0

    def share_record_for_short_id(self, short_id: str) -> dict[str, Any] | None:
        raw_short_id = str(short_id or "")
        if not raw_short_id:
            return None
        now = time.time()
        with self.share_tokens_lock:
            self.prune_inactive_share_tokens_locked(now)
            for token, record in self.share_tokens.items():
                if record.get("revoked") or not self.share_record_sessions_are_active(record):
                    continue
                if not hmac.compare_digest(str(record.get("short_id") or ""), raw_short_id):
                    continue
                result = dict(record)
                result["token"] = token
                return result
        return None

    def revoke_share_tokens_for_session(self, session: str) -> int:
        revoked = 0
        with self.share_tokens_lock:
            for record in self.share_tokens.values():
                if session in self.share_record_sessions(record) and not record.get("revoked"):
                    record["revoked"] = True
                    revoked += 1
        return revoked

    def revoke_share_tokens_for_missing_sessions(self, active_sessions: set[str]) -> int:
        revoked = 0
        now = time.time()
        with self.share_tokens_lock:
            for token, record in list(self.share_tokens.items()):
                if float(record.get("expires_at") or 0.0) <= now:
                    self.share_tokens.pop(token, None)
                    continue
                if not self.share_record_sessions_are_active(record, active_sessions) and not record.get("revoked"):
                    record["revoked"] = True
                    revoked += 1
        return revoked

    def persisted_auto_sessions(self) -> list[str]:
        enabled = read_yolomux_state().get("auto_approve_enabled", [])
        if not isinstance(enabled, list):
            return []
        return [session for session in enabled if isinstance(session, str) and session in self.sessions]

    def set_persisted_auto_session(self, session: str, enabled: bool) -> None:
        state = read_yolomux_state()
        current = state.get("auto_approve_enabled", [])
        sessions = {name for name in current if isinstance(name, str)} if isinstance(current, list) else set()
        if enabled:
            sessions.add(session)
        else:
            sessions.discard(session)
        update_yolomux_state({"auto_approve_enabled": sorted(sessions)})

    def persist_auto_sessions(self) -> None:
        with self.auto_workers_lock:
            worker_sessions = self.auto_worker_session_map()
            local_enabled = {
                worker_sessions.get(name, name)
                for name, worker in self.auto_workers.items()
                if worker.alive()
            }
            local_enabled = {session for session in local_enabled if session in self.sessions}
        current = read_yolomux_state().get("auto_approve_enabled", [])
        if isinstance(current, list):
            external_enabled = {
                session
                for session in current
                if isinstance(session, str) and session not in self.auto_workers and self.auto_approve_session_lock_owner(session)
            }
        else:
            external_enabled = set()
        update_yolomux_state({"auto_approve_enabled": sorted(local_enabled | external_enabled)})

    def notify_status(self) -> dict[str, Any]:
        return {"enabled": bool(read_yolomux_state().get("notify_enabled", False))}

    def settings_payload(self) -> dict[str, Any]:
        return settings_payload()

    def summary_settings(self) -> dict[str, Any]:
        return normalized_summary_settings(self.settings_payload().get("settings"))

    def publish_client_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        trigger: str = "watch",
        cache: str | None = None,
        compute_ms: float | None = None,
    ) -> dict[str, Any]:
        event_payload = dict(payload or {})
        event_payload.setdefault("trigger", trigger)
        if cache is not None:
            event_payload.setdefault("cache", cache)
        if compute_ms is not None:
            event_payload.setdefault("compute_ms", round(max(0.0, compute_ms), 1))
        return self.client_events.publish(event_type, event_payload)

    def client_event_payload_signature(self, payload: Any) -> str:
        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            return str(payload)

    def performance_setting_ms_as_seconds(self, key: str, minimum: float, maximum: float) -> float:
        default = float(DEFAULT_PERFORMANCE_SETTINGS[key])
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get(key, default) if isinstance(performance, dict) else default
        return max(minimum, min(maximum, self.float_value(value, default) / 1000.0))

    def performance_setting_seconds(self, key: str, minimum: float, maximum: float) -> float:
        default = float(DEFAULT_PERFORMANCE_SETTINGS[key])
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get(key, default) if isinstance(performance, dict) else default
        return max(minimum, min(maximum, self.float_value(value, default)))

    def server_event_poll_seconds(self) -> float:
        return self.performance_setting_ms_as_seconds("server_event_poll_ms", 0.25, 60.0)

    def server_directory_event_poll_seconds(self) -> float:
        return self.performance_setting_ms_as_seconds("server_directory_event_poll_ms", 0.25, 60.0)

    def server_background_file_event_poll_seconds(self) -> float:
        return self.performance_setting_ms_as_seconds("server_background_file_event_poll_ms", 0.25, 60.0)

    def server_auto_approve_event_poll_seconds(self) -> float:
        return SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS

    def server_tmux_signal_event_poll_seconds(self) -> float:
        return SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS

    def server_watched_pr_event_poll_seconds(self) -> float:
        return SERVER_WATCHED_PR_EVENT_POLL_SECONDS

    def tmux_signal_snapshot(self, force: bool = False, session: str = "") -> dict[str, Any]:
        target = str(session or "").strip()
        if target:
            return fetch_tmux_signal_snapshot(session=target)
        if not force:
            cached = self.tmux_signal_cache.get_or_miss("snapshot")
            if cached is not CACHE_MISS:
                return copy.deepcopy(cached)
        payload = fetch_tmux_signal_snapshot()
        self.tmux_signal_cache.set("snapshot", copy.deepcopy(payload))
        return payload

    def tmux_signals_payload(self, force: bool = False, session: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        payload = self.tmux_signal_snapshot(force=force, session=session)
        return payload, HTTPStatus.OK if payload.get("ok") else HTTPStatus.SERVICE_UNAVAILABLE

    def tmux_signal_signature_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"generated_at", "compute_ms"}
        }

    def tmux_signal_window_for_target(self, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        raw_target = str(target or "").strip()
        if not raw_target:
            return None
        signal_payload = payload if payload is not None else self.tmux_signal_snapshot()
        windows = signal_payload.get("windows") if isinstance(signal_payload, dict) else None
        if not isinstance(windows, list):
            return None
        if raw_target.startswith("%"):
            for window in windows:
                if not isinstance(window, dict):
                    continue
                panes = window.get("panes")
                if not isinstance(panes, list):
                    continue
                for pane in panes:
                    if isinstance(pane, dict) and raw_target in {str(pane.get("target") or ""), str(pane.get("pane_id") or "")}:
                        return window
            return None
        target = raw_target[:-1] if raw_target.endswith(":") else raw_target
        match = re.fullmatch(r"(?P<session>[^:]+):(?P<window>\d+)(?:\..*)?", target)
        if match:
            key = f"{match.group('session')}:{match.group('window')}"
            return next((window for window in windows if isinstance(window, dict) and window.get("key") == key), None)
        session_windows = [
            window
            for window in windows
            if isinstance(window, dict) and str(window.get("session") or "") == target
        ]
        return next((window for window in session_windows if window.get("active") is True), session_windows[0] if session_windows else None)

    def tmux_signal_pane_for_target(self, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        raw_target = str(target or "").strip()
        if not raw_target:
            return None
        signal_payload = payload if payload is not None else self.tmux_signal_snapshot()
        windows = signal_payload.get("windows") if isinstance(signal_payload, dict) else None
        if not isinstance(windows, list):
            return None
        if raw_target.startswith("%"):
            for window in windows:
                panes = window.get("panes") if isinstance(window, dict) else None
                if not isinstance(panes, list):
                    continue
                match = next((pane for pane in panes if isinstance(pane, dict) and raw_target in {str(pane.get("target") or ""), str(pane.get("pane_id") or "")}), None)
                if match is not None:
                    return match
            return None
        window = self.tmux_signal_window_for_target(raw_target, payload=signal_payload)
        panes = window.get("panes") if isinstance(window, dict) else None
        if not isinstance(panes, list):
            return None
        return next((pane for pane in panes if isinstance(pane, dict) and pane.get("active") is True), panes[0] if panes else None)

    def tmux_snapshot_history_signature(self, target: str) -> tuple[int, int] | None:
        pane = self.tmux_signal_pane_for_target(target)
        if not isinstance(pane, dict):
            return None
        history_size = int(self.float_value(pane.get("history_size"), -1))
        history_bytes = int(self.float_value(pane.get("history_bytes"), -1))
        if history_size < 0 or history_bytes < 0:
            return None
        return history_size, history_bytes

    def tmux_snapshot_capture_lines(self, requested_lines: int, history_signature: tuple[int, int] | None) -> int:
        safe_lines = max(1, min(requested_lines, 1000))
        if history_signature is None:
            return safe_lines
        history_size, _history_bytes = history_signature
        return max(1, min(safe_lines, max(1, history_size)))

    def tmux_signal_window_recently_active(
        self,
        target: str,
        payload: dict[str, Any] | None = None,
        threshold_seconds: float = TMUX_SIGNAL_ACTIVITY_WINDOW_SECONDS,
    ) -> bool:
        window = self.tmux_signal_window_for_target(target, payload=payload)
        if window is None:
            return True
        if window.get("activity_flag") is True:
            return True
        activity_ts = self.float_value(window.get("activity_ts"), 0.0)
        if activity_ts <= 0:
            return True
        return time.time() - activity_ts <= threshold_seconds

    def tmux_recency_ordered_sessions(
        self,
        sessions: list[str] | tuple[str, ...] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> list[str]:
        ordered_source: list[str] = []
        seen: set[str] = set()
        source_sessions = sessions if sessions is not None else self.sessions
        for raw_session in source_sessions:
            session = str(raw_session or "").strip()
            if not session or session in seen:
                continue
            seen.add(session)
            ordered_source.append(session)
        if not ordered_source or "tmux_signal_cache" not in self.__dict__:
            return ordered_source
        signal_payload = payload if payload is not None else self.tmux_signal_snapshot()
        if not isinstance(signal_payload, dict):
            return ordered_source
        original_index = {session: index for index, session in enumerate(ordered_source)}
        scores = {session: 0.0 for session in ordered_source}
        session_records = signal_payload.get("sessions")
        if isinstance(session_records, dict):
            for session in ordered_source:
                record = session_records.get(session)
                if not isinstance(record, dict):
                    continue
                scores[session] = max(
                    scores[session],
                    self.float_value(record.get("activity_ts"), 0.0),
                    self.float_value(record.get("last_attached_ts"), 0.0),
                )
        windows = signal_payload.get("windows")
        if isinstance(windows, list):
            for window in windows:
                if not isinstance(window, dict):
                    continue
                session = str(window.get("session") or "").strip()
                if session not in scores:
                    continue
                scores[session] = max(
                    scores[session],
                    self.float_value(window.get("activity_ts"), 0.0),
                    self.float_value(window.get("session_activity_ts"), 0.0),
                    self.float_value(window.get("session_last_attached_ts"), 0.0),
                )
        return sorted(
            ordered_source,
            key=lambda session: (
                0 if scores[session] > 0 else 1,
                -scores[session],
                original_index[session],
            ),
        )

    def normalized_activity_session_scope(self, value: Any = None) -> str:
        scope = str(value or "configured").strip().lower()
        return "all" if scope in {"all", "tmux", "visible"} else "configured"

    def activity_session_names(self, session_scope: Any = "configured") -> tuple[list[str], list[str], str]:
        scope = self.normalized_activity_session_scope(session_scope)
        if scope != "all":
            return list(self.sessions), [], scope
        sessions, error = list_tmux_session_names()
        if error is not None:
            return list(self.sessions), [error], scope
        seen: set[str] = set()
        ordered: list[str] = []
        for raw_session in sessions:
            session = str(raw_session or "").strip()
            if not session or session in seen:
                continue
            seen.add(session)
            ordered.append(session)
        return ordered, [], scope

    def auto_approve_capture_allowed_for_target(self, target: str) -> bool:
        return self.tmux_signal_window_recently_active(target)

    # --- self-update: hourly check for a newer origin/main + admin-only update+restart -------------
    def updates_settings(self) -> dict[str, Any]:
        settings = settings_payload().get("settings", {})
        section = settings.get("updates", {}) if isinstance(settings, dict) else {}
        return section if isinstance(section, dict) else {}

    def update_notify_level(self, section: dict[str, Any] | None = None) -> str:
        notify_level = str((section or self.updates_settings()).get("notify_level", "patch"))
        return notify_level if notify_level in common.UPDATE_NOTIFY_LEVELS else "patch"

    def update_status_payload(self, dryrun: bool = False) -> dict[str, Any]:
        section = self.updates_settings()
        notify_level = self.update_notify_level(section)
        enabled = notify_level != "none"
        # Only hit the network (git fetch) when actually checking — dryrun, or notifications are not
        # set to none. A disabled boot-time status call stays cheap (local refs only) instead of fetching every load.
        status = common.update_check_status(str(common.PROJECT_ROOT), dryrun=dryrun, fetch=(dryrun or enabled))
        status["enabled"] = enabled
        status["version"] = YOLOMUX_VERSION
        status["notify_level"] = notify_level
        status["notify"] = (dryrun or enabled) and common.update_notify_level_allows(status.get("version_change_level"), notify_level)
        return status

    def perform_self_update(self, dryrun: bool = False) -> dict[str, Any]:
        root = str(common.PROJECT_ROOT)
        plan = ["git pull --ff-only origin main", "python3 tools/static_build.py", "restart server"]
        if dryrun:
            return {"ok": True, "dryrun": True, "restarting": False, "plan": plan,
                    "message": "dryrun: nothing pulled, server not restarted"}
        pull = common.git(["pull", "--ff-only", "origin", "main"], root)
        if pull.returncode != 0:
            # Never force: a dirty/diverged ("read-only") checkout must not be clobbered.
            return {"ok": False, "dryrun": False, "restarting": False, "plan": plan,
                    "error": (pull.stderr or "git pull --ff-only failed").strip()[:400],
                    "message": "update blocked: checkout is not a clean fast-forward; sync it manually"}
        try:
            subprocess.run(["python3", "tools/static_build.py"], cwd=root,
                           capture_output=True, text=True, timeout=120, check=False)
        except Exception:
            pass
        restarting = self._spawn_self_restart()
        return {"ok": True, "dryrun": False, "restarting": restarting, "plan": plan,
                "message": "updated; restarting now" if restarting
                           else "updated; restart spawn failed; restart the server manually"}

    def _resolved_self_restart_argv(self, root: Path) -> list[str]:
        executable = sys.executable or "python3"
        raw_argv = list(sys.argv)
        main_module = sys.modules.get("__main__")
        main_spec = vars(main_module).get("__spec__") if main_module is not None else None
        if main_spec is not None and main_spec.name == "yolomux":
            return [executable, "-m", "yolomux", *raw_argv[1:]]
        if raw_argv[:2] == ["-m", "yolomux"]:
            return [executable, "-m", "yolomux", *raw_argv[2:]]
        entrypoint = raw_argv[0] if raw_argv else "yolomux.py"
        entry_path = Path(entrypoint)
        if entry_path.is_absolute():
            resolved_entrypoint = str(entry_path.resolve())
        else:
            candidate = (root / entry_path).resolve()
            resolved_entrypoint = str(candidate) if candidate.exists() or entry_path.suffix == ".py" else entrypoint
        return [executable, resolved_entrypoint, *raw_argv[1:]]

    def _self_restart_env(self) -> dict[str, str]:
        common.heal_server_path()
        env = {
            key: value
            for key in SELF_RESTART_ENV_KEYS
            if (value := os.environ.get(key)) not in (None, "")
        }
        env["PATH"] = os.environ.get("PATH", env.get("PATH", ""))
        env["TERM"] = os.environ.get("TERM", env.get("TERM", "xterm-256color")) or "xterm-256color"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _self_restart_context(self) -> SelfRestartContext:
        root = Path(common.PROJECT_ROOT).resolve()
        return SelfRestartContext(
            root=str(root),
            argv=self._resolved_self_restart_argv(root),
            env=self._self_restart_env(),
            pid=os.getpid(),
        )

    def _spawn_self_restart(self) -> bool:
        # Restart the checkout that is running this process. The update path pulls and builds in the
        # same PROJECT_ROOT, so dev worktrees can safely bounce themselves without touching prod.
        try:
            context = self._self_restart_context()
            env_cmd = " ".join(
                shlex.quote(item)
                for item in [
                    "env",
                    *(f"{key}={value}" for key, value in context.env.items()),
                    *context.argv,
                ]
            )
            restart_cmd = (
                "sleep 1; "
                f"kill {context.pid} 2>/dev/null || true; "
                "sleep 2; "
                f"kill -9 {context.pid} 2>/dev/null || true; "
                f"cd {shlex.quote(context.root)} && "
                f"nohup {env_cmd} "
                f">> {shlex.quote(context.log_path)} 2>&1 < /dev/null &"
            )
            subprocess.Popen([
                "nohup", "bash", "-lc", restart_cmd,
            ],
                             cwd=context.root, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError as exc:
            logging.warning("self-update restart spawn failed: %s", exc)
            return False

    def update_check_loop(self) -> None:
        # Re-reads settings every iteration so the notification threshold takes effect without a
        # restart. When disabled, idles cheaply. Publishes update_available only when the available
        # target changes, so admins are nudged once per new version, not every interval.
        while True:
            section = self.updates_settings()
            if self.update_notify_level(section) == "none":
                time.sleep(60)
                continue
            try:
                status = self.update_status_payload(dryrun=False)
                target = status.get("target")
                if status.get("available") and status.get("notify") and target and target != getattr(self, "_update_last_target", None):
                    self._update_last_target = target
                    self.publish_client_event("update_available", status, trigger="update-check")
            except Exception:
                pass
            interval_minutes = section.get("check_interval_minutes", 60)
            try:
                interval = max(1.0, float(interval_minutes)) * 60.0
            except (TypeError, ValueError):
                interval = 3600.0
            time.sleep(interval)

    def start_update_check_thread(self) -> bool:
        if getattr(self, "update_check_thread", None) is not None:
            return False
        worker = threading.Thread(target=self.update_check_loop, name="update-check", daemon=True)
        self.update_check_thread = worker
        worker.start()
        return True

    def tabber_activity_refresh_seconds(self) -> float:
        return self.performance_setting_ms_as_seconds("tabber_activity_refresh_ms", 1.0, 60.0)

    def client_event_watch_sleep_seconds(self, now: float) -> float:
        next_due = min(
            self.client_event_next_signature_poll_at,
            self.client_event_next_file_poll_at,
            self.client_event_next_background_file_poll_at,
            self.client_event_next_auto_poll_at,
            self.client_event_next_tmux_signal_poll_at,
            self.client_event_next_watched_pr_poll_at,
            self.client_event_next_yoagent_job_poll_at,
        )
        if next_due <= 0:
            return self.server_event_poll_seconds()
        return max(0.01, min(60.0, next_due - now))

    def update_client_watch_roots(self, roots: Any) -> dict[str, Any]:
        now = time.monotonic()
        payload = roots if isinstance(roots, dict) else {"roots": roots}
        raw_roots = payload.get("roots", []) if isinstance(payload, dict) else []
        unique = self.watch_root_index.normalize_paths(raw_roots)
        normalized_files: list[str] = []
        raw_files = payload.get("files", []) if isinstance(payload, dict) else []
        if isinstance(raw_files, list):
            for item in raw_files:
                path = str(item or "").strip()
                if not path.startswith("/"):
                    continue
                normalized_files.append(str(Path(path).expanduser()))
        unique_files = sorted(set(normalized_files))[:CLIENT_WATCH_FILE_LIMIT]
        active_file_set = set(unique_files)
        normalized_background_files: list[str] = []
        raw_background_files = payload.get("background_files", []) if isinstance(payload, dict) else []
        if isinstance(raw_background_files, list):
            for item in raw_background_files:
                path = str(item or "").strip()
                if not path.startswith("/"):
                    continue
                normalized_background_files.append(str(Path(path).expanduser()))
        unique_background_files = [
            path
            for path in sorted(set(normalized_background_files))
            if path not in active_file_set
        ][:CLIENT_WATCH_FILE_LIMIT]
        context_items = self.normalized_client_context_items(payload.get("context_items", []))
        session_files_requests = self.normalized_client_session_files(payload.get("session_files", []))
        activity_summary = self.normalized_client_activity_summary(payload.get("activity_summary", {}))
        self.watch_root_index.update_client_roots(unique)
        with self.client_watch_lock:
            self.client_watch_files = {path: now + CLIENT_WATCH_ROOT_TTL_SECONDS for path in unique_files}
            self.client_watch_background_files = {path: now + CLIENT_WATCH_ROOT_TTL_SECONDS for path in unique_background_files}
            self.client_watch_context_items = context_items
            self.client_watch_session_files = session_files_requests
            self.client_watch_activity_summary = activity_summary
        self.client_watch_wake_event.set()
        with self.client_events.lock:
            has_client_event_subscribers = bool(self.client_events.subscribers)
        if has_client_event_subscribers:
            self.start_client_watch_snapshot_publish()
        return {
            "ok": True,
            "roots": unique,
            "files": unique_files,
            "background_files": unique_background_files,
            "context_items": context_items,
            "session_files": session_files_requests,
            "activity_summary": activity_summary,
            "mode": "poll",
            "ttl_seconds": CLIENT_WATCH_ROOT_TTL_SECONDS,
        }

    def normalized_client_context_items(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            session = str(item.get("session") or "").strip()
            if not session:
                continue
            messages = int(max(1, min(self.float_value(item.get("messages"), 200), MAX_COMPACT_TRANSCRIPT_ITEMS)))
            key = (session, messages)
            if key in seen:
                continue
            seen.add(key)
            items.append({"session": session, "messages": messages})
        return items[:MAX_YOLOMUX_SESSION_TABS]

    def normalized_client_session_files(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            session = str(item.get("session") or "").strip() or None
            hours = session_files.bounded_session_files_hours(self.float_value(item.get("hours"), 24.0))
            from_ref = str(item.get("from_ref") or item.get("from") or "").strip() or None
            to_ref = str(item.get("to_ref") or item.get("to") or "").strip() or None
            repo_refs = item.get("repo_refs")
            if not isinstance(repo_refs, dict):
                repo_refs = None
            request = {
                "session": session,
                "hours": hours,
                "from_ref": from_ref,
                "to_ref": to_ref,
                "repo_refs": repo_refs,
            }
            signature = self.client_event_payload_signature(request)
            if signature in seen:
                continue
            seen.add(signature)
            items.append(request)
        return items[:MAX_YOLOMUX_SESSION_TABS]

    def normalized_client_activity_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        locale = str(value.get("locale") or "en").strip() or "en"
        visible = value.get("visible") is True
        scope = self.normalized_activity_session_scope(value.get("scope"))
        hours = session_files.bounded_session_files_hours(self.float_value(value.get("hours"), 24.0))
        return {"locale": locale, "visible": visible, "scope": scope, "hours": hours}

    def client_watch_state_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        with self.client_watch_lock:
            return (
                [dict(item) for item in self.client_watch_context_items],
                [dict(item) for item in self.client_watch_session_files],
                dict(self.client_watch_activity_summary),
            )

    def client_watch_roots_snapshot(self) -> list[str]:
        return self.watch_root_index.snapshot()

    def client_watch_files_snapshot(self) -> list[str]:
        now = time.monotonic()
        with self.client_watch_lock:
            current = {path: expires for path, expires in self.client_watch_files.items() if expires > now}
            if len(current) != len(self.client_watch_files):
                self.client_watch_files = current
            return sorted(current)

    def client_watch_background_files_snapshot(self) -> list[str]:
        now = time.monotonic()
        with self.client_watch_lock:
            current = {path: expires for path, expires in self.client_watch_background_files.items() if expires > now}
            if len(current) != len(self.client_watch_background_files):
                self.client_watch_background_files = current
            active = {path for path, expires in self.client_watch_files.items() if expires > now}
            return sorted(path for path in current if path not in active)

    def settings_watch_signature(self) -> tuple[Any, ...]:
        try:
            return file_stat_signature(SETTINGS_PATH)
        except OSError:
            return (str(SETTINGS_PATH), 0, 0)

    def transcripts_watch_signature(self, sessions: dict[str, SessionInfo]) -> tuple[Any, ...]:
        rows: list[tuple[Any, ...]] = []
        for name, info in sorted(sessions.items()):
            for agent in info.agents:
                transcript = str(agent.transcript or "")
                if not transcript:
                    continue
                try:
                    signature = file_stat_signature(Path(transcript))
                except OSError:
                    signature = (transcript, 0, 0)
                rows.append((name, agent.kind or "", agent.session_id or "", signature))
        return tuple(rows)

    def filesystem_roots_for_watch(self, sessions: dict[str, SessionInfo]) -> list[str]:
        self.watch_root_index.update_active_roots(self.active_directory_watch_roots(sessions))
        roots = set(self.client_watch_roots_snapshot())
        settings = settings_payload().get("settings", {})
        file_explorer = settings.get("file_explorer", {}) if isinstance(settings, dict) else {}
        if isinstance(file_explorer, dict):
            for item in file_explorer.get("companion_dirs", []) or []:
                path = str(item or "").strip()
                if path.startswith("/"):
                    roots.add(path)
        return sorted(str(Path(root).expanduser()) for root in roots if str(root or "").startswith("/"))[:CLIENT_WATCH_ROOT_LIMIT]

    def active_directory_watch_roots(self, sessions: dict[str, SessionInfo]) -> dict[str, str]:
        roots: dict[str, str] = {}
        for session, info in sorted((sessions or {}).items()):
            path = ""
            if info.selected_pane and info.selected_pane.current_path:
                path = info.selected_pane.current_path
            if not path:
                agent = next((item for item in info.agents if item.cwd), None)
                path = agent.cwd if agent and agent.cwd else ""
            root = self.active_directory_watch_root(path)
            if root:
                roots[str(session)] = root
        return roots

    def active_directory_watch_root(self, path: str | None) -> str:
        raw = str(path or "").strip()
        if not raw.startswith("/"):
            return ""
        expanded = Path(raw).expanduser()
        git_root = filesystem.git_root_for_path(expanded)
        return git_root or str(expanded)

    def filesystem_roots_watch_signature(self, sessions: dict[str, SessionInfo]) -> tuple[Any, ...]:
        return tuple((root, filesystem_watch_signature(root)) for root in self.filesystem_roots_for_watch(sessions))

    def files_for_watch(self) -> list[str]:
        return self.client_watch_files_snapshot()[:CLIENT_WATCH_FILE_LIMIT]

    def files_watch_signature(self) -> tuple[Any, ...]:
        return tuple((path, file_watch_signature(path)) for path in self.files_for_watch())

    def background_files_for_watch(self) -> list[str]:
        return self.client_watch_background_files_snapshot()[:CLIENT_WATCH_FILE_LIMIT]

    def background_files_watch_signature(self) -> tuple[Any, ...]:
        return tuple((path, file_watch_signature(path)) for path in self.background_files_for_watch())

    def publish_files_changed_event(self, previous: tuple[Any, ...] | None, current: tuple[Any, ...], compute_ms: float = 0.0) -> list[str]:
        if previous == current:
            return []
        previous_by_path = {str(item[0]): item[1] for item in previous or () if isinstance(item, tuple) and len(item) >= 2}
        changed: list[dict[str, Any]] = []
        for item in current:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            path = str(item[0])
            signature = item[1]
            if previous_by_path.get(path) == signature:
                continue
            changed.append({"path": path, "signature": signature})
        if not changed:
            return []
        self.publish_client_event(
            "files_changed",
            {"files": changed, "count": len(changed)},
            trigger="watch",
            cache="ready",
            compute_ms=compute_ms,
        )
        return ["files_changed"]

    def publish_filesystem_ready_event(
        self,
        roots: list[str],
        trigger: str = "watch",
        change_summary: dict[str, Any] | None = None,
    ) -> list[str]:
        if not roots:
            return []
        payload = self.filesystem_push_payload(roots)
        signature = self.client_event_payload_signature(payload.get("directories", []))
        with self.client_watch_lock:
            previous_signature = self.client_watch_filesystem_payload_signature
            self.client_watch_filesystem_payload_signature = signature
        if previous_signature == signature:
            return []
        if change_summary is not None:
            payload["change_summary"] = change_summary
        self.publish_client_event(
            "fs_changed",
            payload,
            trigger=trigger,
            cache="ready",
            compute_ms=float(payload.get("compute_ms") or 0.0),
        )
        return ["fs_changed"]

    def filesystem_push_payload(self, roots: list[str]) -> dict[str, Any]:
        directories: list[dict[str, Any]] = []
        summary = {
            "roots_requested": len(roots[:CLIENT_WATCH_ROOT_LIMIT]),
            "roots_listed": 0,
            "roots_error": 0,
            "entries_listed": 0,
            "files_listed": 0,
            "dirs_listed": 0,
        }
        started = time.perf_counter()
        for root in roots[:CLIENT_WATCH_ROOT_LIMIT]:
            try:
                payload = filesystem.list_directory(root)
            except filesystem.FilesystemError as exc:
                directories.append({"path": root, "status": int(exc.status), "ok": False, "error": str(exc)})
                summary["roots_error"] += 1
                continue
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            if isinstance(entries, list):
                summary["entries_listed"] += len(entries)
                for entry in entries:
                    kind = str(entry.get("kind", "")) if isinstance(entry, dict) else ""
                    if kind == "dir":
                        summary["dirs_listed"] += 1
                    else:
                        summary["files_listed"] += 1
            summary["roots_listed"] += 1
            directories.append({"path": root, "status": 200, "ok": True, "data": payload})
        return {
            "roots": roots,
            "directories": directories,
            "listing_summary": summary,
            "compute_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def clear_transcript_caches(self) -> None:
        with self.transcript_tail_cache_lock:
            self.transcript_tail_cache.clear()
        with self.context_items_cache_lock:
            self.context_items_cache.clear()
        with self.transcripts_payload_cache_lock:
            self.transcripts_payload_cache = None

    def start_client_watch_snapshot_publish(self) -> bool:
        with self.client_watch_lock:
            if self.client_watch_snapshot_running:
                return False
            self.client_watch_snapshot_running = True
        worker = threading.Thread(target=self.publish_client_watch_snapshot, daemon=True)
        worker.start()
        return True

    def publish_client_watch_snapshot(self) -> None:
        try:
            started = time.perf_counter()
            payload = self.build_transcripts_payload()
            self.set_transcripts_payload_cache(payload)
            signature = self.client_event_payload_signature(payload)
            with self.client_watch_lock:
                previous_signature = self.client_watch_transcripts_payload_signature
                self.client_watch_transcripts_payload_signature = signature
            if previous_signature != signature:
                self.publish_client_event(
                    "transcripts_changed",
                    {"signature": signature, "data": payload},
                    trigger="watch_state",
                    cache="ready",
                    compute_ms=(time.perf_counter() - started) * 1000,
                )
            self.publish_context_items_ready_events(trigger="watch_state")
            self.publish_activity_summary_ready_events(trigger="watch_state")
            self.publish_filesystem_ready_event(self.client_watch_roots_snapshot(), trigger="watch_state")
            self.publish_session_files_ready_events(trigger="watch_state")
        finally:
            with self.client_watch_lock:
                self.client_watch_snapshot_running = False

    def poll_client_events_once(self) -> list[str]:
        sessions, _errors = discover_sessions(self.sessions)
        settings_signature = self.settings_watch_signature()
        transcripts_signature = self.transcripts_watch_signature(sessions)
        filesystem_signature = self.filesystem_roots_watch_signature(sessions)
        events: list[str] = []
        with self.client_watch_lock:
            initialized = self.client_watch_initialized
            previous_filesystem_signature = self.client_watch_filesystem_signature
            settings_changed = initialized and self.client_watch_settings_signature != settings_signature
            transcripts_changed = initialized and self.client_watch_transcripts_signature != transcripts_signature
            filesystem_changed = initialized and previous_filesystem_signature != filesystem_signature
            self.client_watch_initialized = True
            self.client_watch_settings_signature = settings_signature
            self.client_watch_transcripts_signature = transcripts_signature
            self.client_watch_filesystem_signature = filesystem_signature
        if settings_changed:
            started = time.perf_counter()
            payload = self.settings_payload()
            self.publish_client_event(
                "settings_changed",
                {"signature": settings_signature, "data": payload},
                cache="ready",
                compute_ms=(time.perf_counter() - started) * 1000,
            )
            events.append("settings_changed")
        if transcripts_changed:
            self.clear_transcript_caches()
            started = time.perf_counter()
            payload = self.build_transcripts_payload()
            self.set_transcripts_payload_cache(payload)
            payload_signature = self.client_event_payload_signature(payload)
            with self.client_watch_lock:
                self.client_watch_transcripts_payload_signature = payload_signature
            self.publish_client_event(
                "transcripts_changed",
                {"signature": transcripts_signature, "data": payload},
                cache="refresh",
                compute_ms=(time.perf_counter() - started) * 1000,
            )
            events.append("transcripts_changed")
            events.extend(self.publish_context_items_ready_events(trigger="transcripts_changed"))
            events.extend(self.publish_activity_summary_ready_events(trigger="transcripts_changed"))
        if filesystem_changed:
            roots = self.filesystem_roots_for_watch(sessions)
            change_summary = filesystem_change_summary(previous_filesystem_signature, filesystem_signature)
            events.extend(self.publish_filesystem_ready_event(roots, change_summary=change_summary))
            session_file_events = self.publish_session_files_ready_events(trigger="fs_changed")
            if session_file_events:
                events.extend(session_file_events)
        return events

    def poll_client_file_events_once(self) -> list[str]:
        started = time.perf_counter()
        files_signature = self.files_watch_signature()
        compute_ms = (time.perf_counter() - started) * 1000
        with self.client_watch_lock:
            initialized = self.client_watch_file_signature is not None
            previous = self.client_watch_file_signature
            self.client_watch_file_signature = files_signature
        if not initialized:
            return []
        return self.publish_files_changed_event(previous, files_signature, compute_ms=compute_ms)

    def poll_client_background_file_events_once(self) -> list[str]:
        started = time.perf_counter()
        files_signature = self.background_files_watch_signature()
        compute_ms = (time.perf_counter() - started) * 1000
        with self.client_watch_lock:
            initialized = self.client_watch_background_file_signature is not None
            previous = self.client_watch_background_file_signature
            self.client_watch_background_file_signature = files_signature
        if not initialized:
            return []
        return self.publish_files_changed_event(previous, files_signature, compute_ms=compute_ms)

    def publish_context_items_ready_events(self, trigger: str = "watch") -> list[str]:
        context_items, _session_files, _activity = self.client_watch_state_snapshot()
        events: list[str] = []
        for item in context_items:
            started = time.perf_counter()
            payload, status = self.context_items(item["session"], int(item["messages"]))
            event_payload = {"session": item["session"], "messages": item["messages"], "status": int(status), "data": payload}
            signature = self.client_event_payload_signature(event_payload)
            key = self.client_event_payload_signature({"session": item["session"], "messages": item["messages"]})
            with self.client_watch_lock:
                previous_signature = self.client_watch_context_item_payload_signatures.get(key)
                self.client_watch_context_item_payload_signatures[key] = signature
            if previous_signature == signature:
                continue
            self.publish_client_event(
                "context_items_ready",
                event_payload,
                trigger=trigger,
                cache="ready",
                compute_ms=(time.perf_counter() - started) * 1000,
            )
            events.append("context_items_ready")
        return events

    def publish_activity_summary_ready_events(self, trigger: str = "watch") -> list[str]:
        if str(trigger or "") not in ACTIVITY_SUMMARY_READY_PUSH_TRIGGERS:
            return []
        _context_items, _session_files, activity_summary = self.client_watch_state_snapshot()
        if activity_summary.get("visible") is not True:
            return []
        started = time.perf_counter()
        payload = self.activity_summary_payload(
            locale=str(activity_summary.get("locale") or "en"),
            session_scope=activity_summary.get("scope"),
            hours=activity_summary.get("hours"),
        )
        stable_payload = {key: value for key, value in payload.items() if key not in {"generated_at", "generated_ts"}}
        signature = self.client_event_payload_signature(stable_payload)
        with self.client_watch_lock:
            previous_signature = self.client_watch_activity_summary_signature
            self.client_watch_activity_summary_signature = signature
        if previous_signature == signature:
            return []
        self.publish_client_event(
            "activity_summary_ready",
            {"locale": payload.get("locale", activity_summary.get("locale") or "en"), "data": payload},
            trigger=trigger,
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["activity_summary_ready"]

    def publish_session_files_ready_events(self, trigger: str = "watch") -> list[str]:
        _context_items, session_files_requests, _activity = self.client_watch_state_snapshot()
        events: list[str] = []
        for item in session_files_requests:
            started = time.perf_counter()
            payload, status = self.session_files_payload(
                item.get("session"),
                self.float_value(item.get("hours"), 24.0),
                from_ref=item.get("from_ref"),
                to_ref=item.get("to_ref"),
                repo_refs=item.get("repo_refs"),
                force=True,
            )
            event_payload = {"request": item, "status": int(status), "data": payload}
            stable_event_payload = copy.deepcopy(event_payload)
            if isinstance(stable_event_payload.get("data"), dict):
                stable_event_payload["data"].pop("cache", None)
            signature = self.client_event_payload_signature(stable_event_payload)
            key = self.client_event_payload_signature(item)
            with self.client_watch_lock:
                previous_signature = self.client_watch_session_file_payload_signatures.get(key)
                self.client_watch_session_file_payload_signatures[key] = signature
            if previous_signature == signature:
                continue
            self.publish_client_event(
                "session_files_ready",
                event_payload,
                trigger=trigger,
                cache="ready",
                compute_ms=(time.perf_counter() - started) * 1000,
            )
            events.append("session_files_ready")
        return events

    def poll_auto_approve_client_event_once(self) -> list[str]:
        started = time.perf_counter()
        payload, status = self.auto_approve_status()
        event_payload = {"status": int(status), "data": payload}
        signature = self.client_event_payload_signature(event_payload)
        with self.client_watch_lock:
            previous = self.client_watch_auto_approve_signature
            self.client_watch_auto_approve_signature = signature
        if previous == signature:
            return []
        self.publish_client_event(
            "auto_approve_changed",
            event_payload,
            trigger="timer",
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["auto_approve_changed"]

    def poll_tmux_signals_client_event_once(self) -> list[str]:
        started = time.perf_counter()
        payload = self.tmux_signal_snapshot(force=True)
        signature = self.client_event_payload_signature(self.tmux_signal_signature_payload(payload))
        with self.client_watch_lock:
            previous = self.client_watch_tmux_signal_signature
            self.client_watch_tmux_signal_signature = signature
        if previous == signature:
            return []
        self.publish_client_event(
            "tmux_signals_changed",
            {"data": payload},
            trigger="timer",
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["tmux_signals_changed"]

    def handle_tmux_signal_event(self, event: dict[str, Any]) -> None:
        self.tmux_signal_cache.clear()
        with self.client_watch_lock:
            self.client_event_next_auto_poll_at = 0.0
            self.client_event_next_tmux_signal_poll_at = 0.0
        self.client_watch_wake_event.set()

    def log_tmux_signal_event_error(self, message: str) -> None:
        self.log_event(None, "tmux_signal_event_error", message, {})

    def start_tmux_signal_event_watcher(self) -> bool:
        with self.client_watch_lock:
            if self.tmux_signal_event_watcher is not None:
                return False
            watcher = TmuxSignalEventWatcher(lambda: list(self.sessions), self.handle_tmux_signal_event, self.log_tmux_signal_event_error)
            self.tmux_signal_event_watcher = watcher
        return watcher.start()

    def stop_tmux_signal_event_watcher(self) -> None:
        with self.client_watch_lock:
            watcher = self.tmux_signal_event_watcher
            self.tmux_signal_event_watcher = None
        if watcher is not None:
            watcher.stop()

    def poll_watched_prs_client_event_once(self) -> list[str]:
        started = time.perf_counter()
        payload = self.watched_prs_payload()
        signature = self.client_event_payload_signature(payload)
        with self.client_watch_lock:
            previous = self.client_watch_watched_prs_signature
            self.client_watch_watched_prs_signature = signature
        if previous == signature:
            return []
        self.publish_client_event(
            "watched_prs_changed",
            {"data": payload},
            trigger="timer",
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["watched_prs_changed"]

    def start_client_event_watcher(self) -> None:
        self.start_tmux_signal_event_watcher()
        with self.client_watch_lock:
            if self.client_watch_running:
                return
            self.client_watch_running = True
        worker = threading.Thread(target=self.client_event_watch_loop, daemon=True)
        self.client_watch_thread = worker
        worker.start()

    def start_client_directory_poll(self) -> bool:
        with self.client_watch_lock:
            if self.client_directory_poll_running:
                return False
            self.client_directory_poll_running = True
        worker = threading.Thread(target=self.run_client_directory_poll_once, daemon=True)
        worker.start()
        return True

    def run_client_directory_poll_once(self) -> None:
        try:
            self.poll_client_events_once()
        except (OSError, RuntimeError, ValueError) as exc:
            self.log_event(None, "client_event_watch_error", f"client directory event watch failed: {exc}", {})
        finally:
            with self.client_watch_lock:
                self.client_directory_poll_running = False

    def client_event_watch_loop(self) -> None:
        while True:
            try:
                now = time.monotonic()
                if now >= self.client_event_next_file_poll_at:
                    self.poll_client_file_events_once()
                    self.client_event_next_file_poll_at = now + self.server_event_poll_seconds()
                if now >= self.client_event_next_background_file_poll_at:
                    self.poll_client_background_file_events_once()
                    self.client_event_next_background_file_poll_at = now + self.server_background_file_event_poll_seconds()
                if now >= self.client_event_next_signature_poll_at:
                    self.client_event_next_signature_poll_at = now + self.server_directory_event_poll_seconds()
                    self.start_client_directory_poll()
                if now >= self.client_event_next_auto_poll_at:
                    self.poll_auto_approve_client_event_once()
                    self.client_event_next_auto_poll_at = now + self.server_auto_approve_event_poll_seconds()
                if now >= self.client_event_next_tmux_signal_poll_at:
                    self.poll_tmux_signals_client_event_once()
                    self.client_event_next_tmux_signal_poll_at = now + self.server_tmux_signal_event_poll_seconds()
                if now >= self.client_event_next_watched_pr_poll_at:
                    self.poll_watched_prs_client_event_once()
                    self.client_event_next_watched_pr_poll_at = now + self.server_watched_pr_event_poll_seconds()
                if now >= self.client_event_next_yoagent_job_poll_at:
                    self.poll_yoagent_jobs_once()
                    self.client_event_next_yoagent_job_poll_at = now + YOAGENT_JOB_POLL_SECONDS
            except (OSError, RuntimeError, ValueError) as exc:
                self.log_event(None, "client_event_watch_error", f"client event watch failed: {exc}", {})
            if self.client_watch_wake_event.wait(self.client_event_watch_sleep_seconds(time.monotonic())):
                self.client_watch_wake_event.clear()

    def cache_set_limited(self, cache: dict[Any, Any], key: Any, value: Any, limit: int) -> None:
        cache[key] = value
        while len(cache) > limit:
            cache.pop(next(iter(cache)))

    def session_files_cache_key(
        self,
        kind: str,
        infos: dict[str, SessionInfo],
        session: str | None,
        hours: float,
        from_ref: str | None,
        to_ref: str | None,
        repo_refs: dict[str, dict[str, str]] | None,
    ) -> tuple[Any, ...]:
        return (
            kind,
            session or "",
            session_files.bounded_session_files_hours(hours),
            str(from_ref or ""),
            str(to_ref or ""),
            repo_refs_cache_signature(repo_refs),
            tuple((name, session_info_cache_signature(info)) for name, info in sorted(infos.items())),
        )

    def session_files_disk_cache_path(self, key: tuple[Any, ...]) -> tuple[Path, str]:
        key_text = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
        signature = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        return SESSION_FILES_CACHE_DIR / f"{signature}.json", signature

    def set_session_files_memory_cache(
        self,
        key: tuple[Any, ...],
        payload: dict[str, Any],
        status: HTTPStatus,
        stored_at: float | None = None,
    ) -> None:
        with self.session_files_cache_lock:
            self.cache_set_limited(
                self.session_files_cache,
                key,
                (time.monotonic() if stored_at is None else stored_at, (copy.deepcopy(payload), status)),
                SESSION_FILES_CACHE_MAX_ITEMS,
            )

    def read_session_files_disk_cache(
        self,
        key: tuple[Any, ...],
        max_age_seconds: float | None = None,
        allow_stale: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus, bool, float] | None:
        path, signature = self.session_files_disk_cache_path(key)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            return None
        if not isinstance(record, dict):
            return None
        if record.get("version") != SESSION_FILES_CACHE_VERSION or record.get("signature") != signature:
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        try:
            status = HTTPStatus(int(record.get("status", int(HTTPStatus.OK))))
            stored_at_wall = float(record.get("stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        age_seconds = max(0.0, time.time() - stored_at_wall)
        fresh = max_age_seconds is None or age_seconds <= max_age_seconds
        if not fresh and not allow_stale:
            return None
        self.set_session_files_memory_cache(key, payload, status, stored_at=time.monotonic() - age_seconds)
        return copy.deepcopy(payload), status, fresh, age_seconds

    def write_session_files_disk_cache_unlocked(
        self,
        path: Path,
        signature: str,
        payload: dict[str, Any],
        status: HTTPStatus,
    ) -> None:
        record = {
            "version": SESSION_FILES_CACHE_VERSION,
            "signature": signature,
            "stored_at": time.time(),
            "status": int(status),
            "payload": payload,
        }
        atomic_write_text(path, json.dumps(record, sort_keys=True, separators=(",", ":")), mode=0o600)

    def write_session_files_disk_cache(self, key: tuple[Any, ...], payload: dict[str, Any], status: HTTPStatus) -> None:
        path, signature = self.session_files_disk_cache_path(key)
        try:
            with file_lock(path, dir_mode=0o700):
                self.write_session_files_disk_cache_unlocked(path, signature, payload, status)
        except OSError as exc:
            logger.warning("failed to write session-files cache %s: %s", path, exc)

    def compute_session_files_cache_entry(
        self,
        key: tuple[Any, ...],
        compute: Callable[[], tuple[dict[str, Any], HTTPStatus]],
    ) -> tuple[dict[str, Any], HTTPStatus, bool, float]:
        path, signature = self.session_files_disk_cache_path(key)
        try:
            with file_lock(path, dir_mode=0o700):
                cached = self.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=False)
                if cached:
                    payload, status, _fresh, age_seconds = cached
                    return payload, status, True, age_seconds
                payload, status = compute()
                self.set_session_files_memory_cache(key, payload, status)
                self.write_session_files_disk_cache_unlocked(path, signature, payload, status)
                return copy.deepcopy(payload), status, False, 0.0
        except OSError as exc:
            logger.warning("failed to lock session-files cache %s: %s", path, exc)
            payload, status = compute()
            self.set_session_files_memory_cache(key, payload, status)
            return copy.deepcopy(payload), status, False, 0.0

    def get_session_files_cache(
        self,
        key: tuple[Any, ...],
        max_age_seconds: float | None = None,
        allow_stale: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus, bool, float] | None:
        now = time.monotonic()
        stale_cached: tuple[dict[str, Any], HTTPStatus, bool, float] | None = None
        with self.session_files_cache_lock:
            cached = self.session_files_cache.get(key)
            if cached:
                stored_at, value = cached
                age_seconds = max(0.0, now - stored_at)
                fresh = max_age_seconds is None or age_seconds <= max_age_seconds
                payload, status = value
                if fresh:
                    return copy.deepcopy(payload), status, True, age_seconds
                stale_cached = (copy.deepcopy(payload), status, False, age_seconds)
        disk_cached = self.read_session_files_disk_cache(key, max_age_seconds=max_age_seconds, allow_stale=allow_stale)
        if disk_cached:
            if stale_cached is None or disk_cached[3] <= stale_cached[3]:
                return disk_cached
        if stale_cached is not None and allow_stale:
            return stale_cached
        return None

    def set_session_files_cache(self, key: tuple[Any, ...], payload: dict[str, Any], status: HTTPStatus) -> None:
        self.set_session_files_memory_cache(key, payload, status)
        self.write_session_files_disk_cache(key, payload, status)

    def clear_session_files_cache(self) -> None:
        with self.session_files_cache_lock:
            self.session_files_cache.clear()
            self.session_files_refreshing_cache_keys.clear()

    def refresh_session_files_payload_cache(
        self,
        cache_key: tuple[Any, ...],
        session: str | None,
        infos: dict[str, SessionInfo],
        hours: float,
        from_ref: str | None,
        to_ref: str | None,
        repo_refs: dict[str, dict[str, str]] | None,
    ) -> None:
        try:
            self.compute_session_files_cache_entry(
                cache_key,
                lambda: session_files.session_files_payload(
                    session,
                    infos,
                    hours,
                    from_ref=from_ref,
                    to_ref=to_ref,
                    repo_refs=repo_refs,
                    include_cross_session_attribution=not bool(session),
                ),
            )
        finally:
            with self.session_files_cache_lock:
                self.session_files_refreshing_cache_keys.discard(cache_key)

    def refresh_session_files_info_cache(
        self,
        cache_key: tuple[Any, ...],
        info: SessionInfo,
        hours: float,
        from_ref: str | None,
        to_ref: str | None,
        repo_refs: dict[str, dict[str, str]] | None,
    ) -> None:
        try:
            self.compute_session_files_cache_entry(
                cache_key,
                lambda: (session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs), HTTPStatus.OK),
            )
        finally:
            with self.session_files_cache_lock:
                self.session_files_refreshing_cache_keys.discard(cache_key)

    def start_session_files_cache_refresh(self, cache_key: tuple[Any, ...], target: Any, *args: Any) -> bool:
        with self.session_files_cache_lock:
            if cache_key in self.session_files_refreshing_cache_keys:
                return False
            self.session_files_refreshing_cache_keys.add(cache_key)
        worker = threading.Thread(target=target, args=(cache_key, *args), daemon=True)
        worker.start()
        return True

    def cached_session_files_payload_for_info(
        self,
        info: SessionInfo,
        hours: float = 24.0,
        from_ref: str | None = None,
        to_ref: str | None = None,
        repo_refs: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        infos = {info.session: info}
        key = self.session_files_cache_key("payload", infos, info.session, hours, from_ref, to_ref, repo_refs)
        cached = self.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=True)
        if cached:
            payload, _status, fresh, _age = cached
            if not fresh:
                self.start_session_files_cache_refresh(key, self.refresh_session_files_info_cache, info, hours, from_ref, to_ref, repo_refs)
            return payload
        payload, _status, _hit, _age = self.compute_session_files_cache_entry(
            key,
            lambda: (session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs), HTTPStatus.OK),
        )
        return copy.deepcopy(payload)

    def cached_session_files_payloads_for_infos(
        self,
        infos: dict[str, SessionInfo],
        hours: float = 24.0,
        from_ref: str | None = None,
        to_ref: str | None = None,
        repo_refs: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not infos:
            return {}
        if len(infos) == 1:
            session, info = next(iter(infos.items()))
            return {session: self.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)}
        payloads: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=session_files_batch_worker_count(len(infos)), thread_name_prefix="session-files-warm") as executor:
            futures = {
                executor.submit(self.cached_session_files_payload_for_info, info, hours, from_ref, to_ref, repo_refs): session
                for session, info in infos.items()
            }
            for future in as_completed(futures):
                payloads[futures[future]] = future.result()
        return payloads

    def session_files_payload_for_infos(
        self,
        session: str | None,
        infos: dict[str, SessionInfo],
        hours: float,
        from_ref: str | None = None,
        to_ref: str | None = None,
        repo_refs: dict[str, dict[str, str]] | None = None,
        force: bool = False,
        extra_errors: list[str] | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        cache_key = self.session_files_cache_key("payload", infos, session, hours, from_ref, to_ref, repo_refs)
        max_age = SESSION_FILES_CACHE_SECONDS
        cached = None if force else self.get_session_files_cache(cache_key, max_age_seconds=max_age, allow_stale=True)
        cache_meta: dict[str, Any]
        if cached:
            payload, status, fresh, age_seconds = cached
            cache_meta = {
                "hit": True,
                "stale": not fresh,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": max_age,
            }
            if not fresh:
                refreshing = self.start_session_files_cache_refresh(cache_key, self.refresh_session_files_payload_cache, session, infos, hours, from_ref, to_ref, repo_refs)
                cache_meta["refreshing"] = refreshing
        else:
            payload, status, cache_hit, age_seconds = self.compute_session_files_cache_entry(
                cache_key,
                lambda: session_files.session_files_payload(
                    session,
                    infos,
                    hours,
                    from_ref=from_ref,
                    to_ref=to_ref,
                    repo_refs=repo_refs,
                    include_cross_session_attribution=not bool(session),
                ),
            )
            cache_meta = {
                "hit": cache_hit,
                "stale": False,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": max_age,
                "refreshing": False,
            }
        payload = copy.deepcopy(payload)
        payload["errors"] = [*(extra_errors or []), *payload.get("errors", [])]
        payload["cache"] = cache_meta
        return payload, status

    def get_transcripts_payload_cache(self, max_age_seconds: float, allow_stale: bool = False) -> tuple[dict[str, Any], bool, float] | None:
        now = time.monotonic()
        with self.transcripts_payload_cache_lock:
            cached = self.transcripts_payload_cache
            if cached is None:
                return None
            stored_at, payload = cached
            age_seconds = max(0.0, now - stored_at)
            fresh = age_seconds <= max_age_seconds
            if not fresh and not allow_stale:
                return None
            return copy.deepcopy(payload), fresh, age_seconds

    def set_transcripts_payload_cache(self, payload: dict[str, Any]) -> None:
        with self.transcripts_payload_cache_lock:
            self.transcripts_payload_cache = (time.monotonic(), copy.deepcopy(payload))

    def start_transcripts_payload_refresh(self) -> bool:
        with self.transcripts_payload_cache_lock:
            if self.transcripts_payload_refreshing:
                return False
            self.transcripts_payload_refreshing = True
        worker = threading.Thread(target=self.refresh_transcripts_payload_cache, daemon=True)
        worker.start()
        return True

    def refresh_transcripts_payload_cache(self) -> None:
        try:
            self.set_transcripts_payload_cache(self.build_transcripts_payload())
        finally:
            with self.transcripts_payload_cache_lock:
                self.transcripts_payload_refreshing = False

    def watched_prs_payload(self, allow_network: bool = True) -> dict[str, Any]:
        # resolve the github.watched_prs watchlist to live PR metadata, independent of any open
        # session's branch. The server-side SSE loop refreshes it on a fixed slow cadence so a big watchlist
        # does not exhaust the GitHub rate limit.
        settings = settings_payload().get("settings", {})
        refs = settings.get("github", {}).get("watched_prs", [])
        result = watched_pr_metadata(refs, self.metadata_cache, allow_network=allow_network)
        # log the truncation only when the capped state CHANGES (count or watchlist), not on
        # every poll — otherwise the event log fills with one identical entry per refresh.
        truncated = result["truncated"]
        signature = (truncated, tuple(str(ref) for ref in refs)) if truncated else None
        if signature != self._watched_pr_truncated_signature:
            self._watched_pr_truncated_signature = signature
            if truncated:
                self.log_event(
                    None,
                    "watched_pr_truncated",
                    f"watched PR list capped: {truncated} entries beyond the limit are not polled",
                    {"truncated": truncated},
                )
        return {
            "watched_prs": result["watched_prs"],
            "truncated": result["truncated"],
            "invalid": result["invalid"],
        }

    def tabber_activity_agents_snapshot(self, force: bool = False) -> list[dict[str, Any]]:
        if force:
            payload = self.refresh_tabber_activity_cache()
            agents = payload.get("agents") if isinstance(payload, dict) else []
            return copy.deepcopy(agents) if isinstance(agents, list) else []
        cached = self.get_tabber_activity_cache(self.tabber_activity_refresh_seconds(), allow_stale=True)
        if cached:
            payload, _fresh, _age_seconds = cached
            agents = payload.get("agents") if isinstance(payload, dict) else []
            return copy.deepcopy(agents) if isinstance(agents, list) else []
        payload = self.refresh_tabber_activity_cache()
        agents = payload.get("agents") if isinstance(payload, dict) else []
        return copy.deepcopy(agents) if isinstance(agents, list) else []

    def activity_session_info_payload(
        self,
        session: str,
        info: SessionInfo,
        project: dict[str, Any],
        files_payload: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        selected = info.selected_pane
        agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        git_data = project.get("git") if isinstance(project.get("git"), dict) else None
        pull_request = project.get("pull_request") if isinstance(project.get("pull_request"), dict) else None
        rolling = self.yoagent_session_summary_record(session)
        latest_summary = str(rolling.get("rolling_summary") or summary.get("local") or "").strip()
        return {
            "session": session,
            "path": str((git_data or {}).get("root") or (git_data or {}).get("cwd") or (agent.cwd if agent else "") or (selected.current_path if selected else "")),
            "cwd": str((agent.cwd if agent else "") or (selected.current_path if selected else "")),
            "tmux_target": str(selected.target if selected else ""),
            "agent": self.compact_agent_for_run_history(agent),
            "git": git_data,
            "pull_request": pull_request,
            "ci": pull_request.get("checks") if isinstance(pull_request, dict) and isinstance(pull_request.get("checks"), dict) else None,
            "linear": project.get("linear") if isinstance(project.get("linear"), list) else [],
            "files": summary.get("files") if isinstance(summary.get("files"), dict) else {},
            "recent_paths": build_recent_agents_payload({session: info}, [session], session_files_by_session={session: files_payload}),
            "latest_summary": truncate_text(latest_summary, 1200),
            "latest_summary_updated_ts": max(0.0, self.float_value(rolling.get("updated_ts"), 0.0)),
            "recent_events": self.event_log.tail(session=session, limit=5),
            "project": project,
        }

    def activity_summary_payload(self, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        locale = str(locale or "en").strip() or "en"
        session_names, scope_errors, scope = self.activity_session_names(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        sessions, errors = discover_sessions(session_names)
        errors = [*scope_errors, *errors]
        ordered_sessions = self.tmux_recency_ordered_sessions(session_names)
        self.warm_metadata_cache_async(sessions)
        self.prune_yoagent_session_summaries(set(sessions))
        summaries: dict[str, Any] = {}
        ordered_summaries: list[dict[str, Any]] = []
        session_files_by_session: dict[str, dict[str, Any]] = {}
        session_info: dict[str, Any] = {}
        with self.activity_summary_lock:
            if force:
                self.activity_summary_cache.clear()
                self.clear_session_files_cache()
            for session in ordered_sessions:
                info = sessions.get(session)
                if info is None:
                    continue
                project = session_project_metadata(info, self.metadata_cache, allow_network=False)
                files_payload = self.cached_session_files_payload_for_info(info, hours=bounded_hours)
                session_files_by_session[session] = files_payload
                signature = activity_signature(info, project, files_payload)
                cached = self.activity_summary_cache.get(session)
                if cached and cached.get("signature") == signature:
                    summary = dict(cached["summary"])
                else:
                    summary = build_session_activity_summary(info, project, files_payload)
                    self.activity_summary_cache[session] = {"signature": signature, "summary": summary}
                    summary = dict(summary)
                self.attach_yoagent_session_summary(session, summary)
                summaries[session] = summary
                ordered_summaries.append(summary)
                session_info[session] = self.activity_session_info_payload(session, info, project, files_payload, summary)
            for session in list(self.activity_summary_cache):
                if session not in sessions:
                    self.activity_summary_cache.pop(session, None)
        generated = datetime.now(timezone.utc)
        rolling_updated = self.latest_yoagent_session_summary_updated_ts()
        return {
            "generated_at": generated.isoformat(),
            "generated_ts": generated.timestamp(),
            "session_order": [session for session in ordered_sessions if session in summaries],
            "sessions": summaries,
            "session_info": session_info,
            "agents": self.tabber_activity_agents_snapshot(force=force),
            "global": build_global_activity_summary(ordered_summaries, errors),
            "capabilities": yoagent_capabilities_payload(),
            "errors": errors,
            "locale": locale,
            "session_scope": scope,
            "session_file_hours": bounded_hours,
            "yoagent_summaries": {
                "mode": "first_launch",
                "first_launch_started": bool(self.yoagent_summary_first_launch_started),
                "running": bool(self.yoagent_summary_worker_running),
                "updated_ts": rolling_updated,
                "updated_at": datetime.fromtimestamp(rolling_updated, timezone.utc).isoformat() if rolling_updated else "",
            },
        }

    def sanitized_yoagent_session_summaries(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.sanitized_yoagent_session_summaries(*args, **kwargs)

    def load_yoagent_session_summaries(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.load_yoagent_session_summaries(*args, **kwargs)

    def persist_yoagent_session_summaries_locked(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.persist_yoagent_session_summaries_locked(*args, **kwargs)

    def prune_yoagent_session_summaries(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.prune_yoagent_session_summaries(*args, **kwargs)

    def attach_yoagent_session_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.attach_yoagent_session_summary(*args, **kwargs)

    def latest_yoagent_session_summary_updated_ts(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.latest_yoagent_session_summary_updated_ts(*args, **kwargs)

    def float_value(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def build_yoagent_session_summary_update_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.build_yoagent_session_summary_update_prompt(*args, **kwargs)

    def parse_yoagent_session_summary_response(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.parse_yoagent_session_summary_response(*args, **kwargs)

    def yoagent_codex_app_server_target(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_codex_app_server_target(*args, **kwargs)

    def yoagent_codex_app_server_target_key(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_codex_app_server_target_key(*args, **kwargs)

    def close_yoagent_codex_app_server(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.close_yoagent_codex_app_server(*args, **kwargs)

    def ensure_yoagent_codex_app_server(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.ensure_yoagent_codex_app_server(*args, **kwargs)

    def run_yoagent_codex_app_server(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.run_yoagent_codex_app_server(*args, **kwargs)

    def run_yoagent_direct_prompt_backend(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.run_yoagent_direct_prompt_backend(*args, **kwargs)

    def update_yoagent_session_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.update_yoagent_session_summary(*args, **kwargs)

    def tick_yoagent_session_summaries(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.tick_yoagent_session_summaries(*args, **kwargs)

    def maybe_start_yoagent_summary_worker(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.maybe_start_yoagent_summary_worker(*args, **kwargs)

    def yoagent_summary_worker_loop(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_summary_worker_loop(*args, **kwargs)

    def yoagent_settings(self) -> dict[str, Any]:
        settings = settings_payload().get("settings", {}).get("yoagent", {})
        return settings if isinstance(settings, dict) else {}

    def yoagent_skills_payload(self) -> dict[str, Any]:
        return load_yoagent_skills()

    def yoagent_skill_files_payload(self, kind: str = "", name: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        try:
            if name:
                return {"ok": True, "file": read_user_skill_file(kind or "skill", name), "skills": self.yoagent_skills_payload()}, HTTPStatus.OK
            return list_user_skill_files(), HTTPStatus.OK
        except ValueError as exc:
            return {"error": str(exc)}, HTTPStatus.BAD_REQUEST
        except FileNotFoundError:
            return {"kind": kind, "name": name, "error": "skill file not found"}, HTTPStatus.NOT_FOUND
        except OSError as exc:
            return {"kind": kind, "name": name, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR

    def upsert_yoagent_skill_file(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        kind = str(payload.get("kind") or "skill")
        name = str(payload.get("name") or payload.get("file") or "")
        text = str(payload.get("text") or payload.get("content") or "")
        try:
            item = write_user_skill_file(kind, name, text)
        except ValueError as exc:
            return {"kind": kind, "name": name, "error": str(exc)}, HTTPStatus.BAD_REQUEST
        except OSError as exc:
            return {"kind": kind, "name": name, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR
        self.log_event(None, "yoagent_skill_file_upserted", f"YO!agent skill file updated: {item.get('path')}", {
            "kind": item.get("kind"),
            "name": item.get("name"),
            "path": item.get("path"),
        })
        self.publish_client_event("yoagent_skills_changed", {"kind": item.get("kind"), "name": item.get("name"), "path": item.get("path")}, trigger="yoagent_skill_file", cache="ready")
        return {"ok": True, "file": item, "skills": self.yoagent_skills_payload()}, HTTPStatus.OK

    def delete_yoagent_skill_file(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        kind = str(payload.get("kind") or "skill")
        name = str(payload.get("name") or payload.get("file") or "")
        try:
            item = delete_user_skill_file(kind, name)
        except ValueError as exc:
            return {"kind": kind, "name": name, "error": str(exc)}, HTTPStatus.BAD_REQUEST
        except FileNotFoundError:
            return {"kind": kind, "name": name, "error": "skill file not found"}, HTTPStatus.NOT_FOUND
        except OSError as exc:
            return {"kind": kind, "name": name, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR
        self.log_event(None, "yoagent_skill_file_deleted", f"YO!agent skill file deleted: {item.get('path')}", {
            "kind": item.get("kind"),
            "name": item.get("name"),
            "path": item.get("path"),
        })
        self.publish_client_event("yoagent_skills_changed", {"kind": item.get("kind"), "name": item.get("name"), "path": item.get("path"), "deleted": True}, trigger="yoagent_skill_file", cache="ready")
        return {"ok": True, "file": item, "skills": self.yoagent_skills_payload()}, HTTPStatus.OK

    def yoagent_skill_file_answer(self, intent: dict[str, Any]) -> str:
        operation = str(intent.get("operation") or "")
        kind = str(intent.get("kind") or "skill")
        name = str(intent.get("name") or "")
        if operation == "list":
            payload, status = self.yoagent_skill_files_payload()
            if status != HTTPStatus.OK:
                return f"I could not list YO!skills: {payload.get('error') or status.phrase}."
            dirs = payload.get("user_dirs") if isinstance(payload.get("user_dirs"), dict) else {}
            skills_payload = self.yoagent_skills_payload()
            builtin_dirs = skills_payload.get("builtin_dirs") if isinstance(skills_payload.get("builtin_dirs"), dict) else {}
            files = [item for item in payload.get("files", []) if isinstance(item, dict)]
            rows = [f"- `{item.get('kind')}` `{item.get('name')}` at `{item.get('path')}`" for item in files[:20]]
            body = "\n".join(rows) if rows else "- No user-local YO!skill or context files exist yet."
            return "\n".join([
                "User-local YO!skills and context files:",
                "",
                f"- built-in skills: `{builtin_dirs.get('skills') or ''}`",
                f"- built-in context: `{builtin_dirs.get('context') or ''}`",
                f"- skills: `{dirs.get('skills') or ''}`",
                f"- context: `{dirs.get('context') or ''}`",
                "",
                body,
            ])
        if operation == "read":
            payload, status = self.yoagent_skill_files_payload(kind, name)
            if status != HTTPStatus.OK:
                return f"I could not read `{name}`: {payload.get('error') or status.phrase}."
            item = payload.get("file") if isinstance(payload.get("file"), dict) else {}
            text = truncate_text(str(item.get("text") or ""), 4000)
            return f"Read `{item.get('path')}`:\n\n```text\n{text}\n```"
        if operation == "delete":
            payload, status = self.delete_yoagent_skill_file({"kind": kind, "name": name})
            if status != HTTPStatus.OK:
                return f"I could not delete `{name}`: {payload.get('error') or status.phrase}."
            item = payload.get("file") if isinstance(payload.get("file"), dict) else {}
            return f"Deleted user-local `{item.get('kind')}` `{item.get('name')}` at `{item.get('path')}`."
        if operation == "upsert":
            payload, status = self.upsert_yoagent_skill_file({"kind": kind, "name": name, "text": intent.get("text") or ""})
            if status != HTTPStatus.OK:
                return f"I could not update `{name}`: {payload.get('error') or status.phrase}."
            item = payload.get("file") if isinstance(payload.get("file"), dict) else {}
            return f"Updated user-local `{item.get('kind')}` `{item.get('name')}` at `{item.get('path')}`."
        return "I could not determine which YO!skill file operation to perform."

    def yoagent_conversation_payload(self) -> dict[str, Any]:
        messages = yoagent_conversation.load_messages()
        with self.yoagent_action_lock:
            active_action_ids = set(self.yoagent_action_previews)
            pending_waits = [copy.deepcopy(wait) for wait in self.yoagent_action_waits.values()]
        for message in messages:
            actions = message.get("actions")
            if not isinstance(actions, list):
                continue
            next_actions = []
            for action in actions:
                item = copy.deepcopy(action) if isinstance(action, dict) else action
                if (
                    isinstance(item, dict)
                    and item.get("id")
                    and item.get("status") == "ready"
                    and str(item.get("id")) not in active_action_ids
                ):
                    item["status"] = "expired"
                    item["status_text"] = "action expired; ask again to create a fresh send"
                next_actions.append(item)
            message["actions"] = next_actions
        return {
            "ok": True,
            "messages": messages,
            "transcript_path": str(yoagent_conversation.YOAGENT_CONVERSATION_PATH),
            "transcript_display_path": yoagent_conversation.display_path(yoagent_conversation.YOAGENT_CONVERSATION_PATH),
            "resume_backends": sorted(self.yoagent_cli_sessions),
            "pending_waits": sorted(pending_waits, key=lambda item: float(item.get("started_ts") or 0)),
        }

    def record_yoagent_message(
        self,
        role: str,
        content: str,
        *,
        actions: list[dict[str, Any]] | None = None,
        created_at: str | None = None,
        kind: str = "",
        session: str = "",
        details: str = "",
        response_ms: float | None = None,
        auxiliary_lines: list[str] | None = None,
        auxiliary_preview: str = "",
        stream_items: list[dict[str, str]] | None = None,
        auxiliary_done: bool = False,
        auxiliary_truncated: bool = False,
    ) -> dict[str, Any] | None:
        clean_content = redacted_action_text(str(content or ""), 100_000)
        message: dict[str, Any] = {"role": role, "content": clean_content, "createdAt": created_at or datetime.now(timezone.utc).isoformat()}
        if actions:
            message["actions"] = actions
        if kind:
            message["kind"] = kind
        if session:
            message["session"] = session
        if details:
            message["details"] = redacted_action_text(str(details), 10_000)
        if isinstance(response_ms, (int, float)) and float(response_ms) > 0:
            message["responseMs"] = round(float(response_ms), 3)
        clean_auxiliary_lines = [redacted_action_text(str(line or ""), None) for line in (auxiliary_lines or []) if str(line or "").strip()]
        clean_stream_items = self.sanitized_yoagent_stream_items(stream_items)
        if clean_auxiliary_lines:
            message["auxiliaryLines"] = clean_auxiliary_lines
            message["auxiliaryText"] = "\n".join(message["auxiliaryLines"])
            message["auxiliaryPreview"] = redacted_action_text(str(auxiliary_preview or "\n".join(message["auxiliaryLines"][-1:])), None)
        if clean_stream_items:
            message["streamItems"] = clean_stream_items
        if (clean_auxiliary_lines or clean_stream_items) and auxiliary_done:
            message["auxiliaryDone"] = True
        if (clean_auxiliary_lines or clean_stream_items) and auxiliary_truncated:
            message["auxiliaryTruncated"] = True
        return yoagent_conversation.append_message(message)

    def publish_yoagent_conversation_changed(self, trigger: str = "yoagent") -> None:
        self.publish_client_event("yoagent_conversation_changed", {"reason": trigger}, trigger=trigger, cache="ready")

    def publish_yoagent_stream_delta(
        self,
        stream_id: str,
        content: str,
        *,
        backend: str = "",
        phase: str = "",
        done: bool = False,
        hidden_thinking_removed: bool = False,
        events: list[dict[str, Any]] | None = None,
        auxiliary_lines: list[str] | None = None,
        auxiliary_preview: str = "",
        stream_items: list[dict[str, str]] | None = None,
        hidden_work_active: bool = False,
        tool_active: bool = False,
        auxiliary_done: bool = False,
        auxiliary_truncated: bool = False,
        turn_done: bool = False,
        error: bool = False,
        aborted: bool = False,
        created_at: str = "",
    ) -> None:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return
        payload: dict[str, Any] = {
            "stream_id": safe_stream_id,
            "content": truncate_text(str(content or ""), 20_000),
            "done": bool(done),
            "running": not bool(done),
            "turn_done": bool(turn_done or done),
            "error": bool(error),
            "aborted": bool(aborted),
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
        if backend:
            payload["backend"] = backend
        if phase:
            payload["phase"] = phase
        if hidden_thinking_removed:
            payload["hidden_thinking_removed"] = True
        if events:
            payload["events"] = events
        if auxiliary_lines:
            payload["auxiliary_lines"] = [str(line or "") for line in auxiliary_lines if str(line or "").strip()]
        if auxiliary_preview:
            payload["auxiliary_preview"] = str(auxiliary_preview)
        clean_stream_items = self.sanitized_yoagent_stream_items(stream_items)
        if clean_stream_items:
            payload["stream_items"] = clean_stream_items
        if hidden_work_active:
            payload["hidden_work_active"] = True
        if tool_active:
            payload["tool_active"] = True
        if auxiliary_done:
            payload["auxiliary_done"] = True
        if auxiliary_truncated:
            payload["auxiliary_truncated"] = True
        self.publish_client_event("yoagent_stream_delta", payload, trigger="yoagent_stream", cache="delta")

    def yoagent_stream_auxiliary_message_fields(self, stream_id: str) -> dict[str, Any]:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return {}
        with self.yoagent_stream_lock:
            state = copy.deepcopy(self.yoagent_stream_states.get(safe_stream_id) or {})
        lines = [str(line or "") for line in state.get("auxiliaryLines", []) if str(line or "").strip()]
        stream_items = self.sanitized_yoagent_stream_items(state.get("streamItems"))
        if not lines and not stream_items:
            return {}
        fields: dict[str, Any] = {"stream_items": stream_items}
        if lines:
            fields["auxiliary_lines"] = lines
            fields["auxiliary_preview"] = str(state.get("auxiliaryPreview") or "\n".join(lines[-1:]))
        if bool(state.get("auxiliaryDone")):
            fields["auxiliary_done"] = True
        if bool(state.get("auxiliaryTruncated")):
            fields["auxiliary_truncated"] = True
        return fields

    def sanitized_yoagent_stream_items(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in {"assistant", "thinking", "tool"}:
                continue
            text = redacted_action_text(str(item.get("text") or ""), None)
            if text.strip():
                result.append({"kind": kind, "text": text})
        return result

    def yoagent_stream_callback(self, stream_id: str, backend: str) -> Any:
        state: dict[str, Any] = {
            "raw_content": "",
            "last_content": None,
            "hidden_thinking_removed": False,
            "auxiliary_lines": [],
            "hidden_work_prefix": "",
            "hidden_work_text": "",
            "hidden_work_active": False,
            "tool_active": False,
            "auxiliary_done": False,
            "auxiliary_truncated": False,
            "stream_items": [],
        }

        def publish(events: list[dict[str, Any]], phase: str = "") -> None:
            lines = list(state.get("auxiliary_lines") or [])
            stream_items = self.sanitized_yoagent_stream_items(state.get("stream_items"))
            active = bool(state.get("hidden_work_active") or state.get("tool_active"))
            preview_count = 2 if active else 1
            preview = "\n".join(lines[-preview_count:]) if lines else ""
            safe_stream_id = str(stream_id or "").strip()
            if safe_stream_id:
                with self.yoagent_stream_lock:
                    self.yoagent_stream_states[safe_stream_id] = {
                        "auxiliaryLines": lines,
                        "auxiliaryText": "\n".join(lines),
                        "auxiliaryPreview": preview,
                        "streamItems": stream_items,
                        "auxiliaryDone": bool(state.get("auxiliary_done")),
                        "auxiliaryTruncated": bool(state.get("auxiliary_truncated")),
                        "updatedTs": time.time(),
                    }
                    if len(self.yoagent_stream_states) > 50:
                        oldest = sorted(self.yoagent_stream_states.items(), key=lambda item: float(item[1].get("updatedTs") or 0))[:10]
                        for key, _value in oldest:
                            self.yoagent_stream_states.pop(key, None)
            self.publish_yoagent_stream_delta(
                stream_id,
                str(state.get("last_content") or ""),
                backend=backend,
                phase=phase,
                hidden_thinking_removed=bool(state.get("hidden_thinking_removed")),
                events=events,
                auxiliary_lines=lines,
                auxiliary_preview=preview,
                stream_items=stream_items,
                hidden_work_active=bool(state.get("hidden_work_active")),
                tool_active=bool(state.get("tool_active")),
                auxiliary_done=bool(state.get("auxiliary_done")),
                auxiliary_truncated=bool(state.get("auxiliary_truncated")),
                turn_done=phase == "done",
                error=phase == "error",
            )

        def hidden_work_prefix(event: dict[str, Any]) -> str:
            if event.get("summary"):
                return "thinking summary"
            if event.get("redacted"):
                return "redacted thinking"
            return "thinking"

        def compact_auxiliary_text(value: str) -> str:
            return " ".join(str(value or "").split())

        def append_stream_item(kind: str, text: str, *, merge: bool = True) -> None:
            safe_kind = str(kind or "").strip().lower()
            value = redacted_action_text(str(text or ""), None)
            if safe_kind not in {"assistant", "thinking", "tool"} or not value:
                return
            items = self.sanitized_yoagent_stream_items(state.get("stream_items"))
            if merge and items and items[-1].get("kind") == safe_kind:
                joiner = "" if safe_kind == "assistant" else "\n"
                items[-1]["text"] = str(items[-1].get("text") or "") + joiner + value
            else:
                items.append({"kind": safe_kind, "text": value})
            state["stream_items"] = items

        def replace_or_append_stream_item(kind: str, text: str) -> None:
            safe_kind = str(kind or "").strip().lower()
            value = redacted_action_text(str(text or ""), None)
            if safe_kind not in {"assistant", "thinking", "tool"} or not value:
                return
            items = self.sanitized_yoagent_stream_items(state.get("stream_items"))
            if items and items[-1].get("kind") == safe_kind:
                items[-1]["text"] = value
            else:
                items.append({"kind": safe_kind, "text": value})
            state["stream_items"] = items

        def hidden_work_stream_item_text(event: dict[str, Any]) -> str:
            prefix = str(state.get("hidden_work_prefix") or hidden_work_prefix(event) or "thinking")
            raw_text = str(state.get("hidden_work_text") or event.get("text") or "")
            compact_text = compact_auxiliary_text(raw_text)
            if raw_text:
                return f"{prefix}: {raw_text}"
            if compact_text:
                return f"{prefix}: {compact_text}"
            return yoagent_stream_event_auxiliary_line(event)

        def append_auxiliary_line(event: dict[str, Any]) -> None:
            line = yoagent_stream_event_auxiliary_line(event)
            if not line:
                return
            event_type = str(event.get("kind") or event.get("event") or "")
            lines = list(state.get("auxiliary_lines") or [])
            if event_type == HIDDEN_WORK_DELTA:
                prefix = hidden_work_prefix(event)
                raw_text = str(event.get("text") or "")
                compact_text = compact_auxiliary_text(raw_text)
                if not compact_text:
                    compact_text = compact_auxiliary_text(line.removeprefix(f"{prefix}:").strip()) or prefix
                previous_prefix = str(state.get("hidden_work_prefix") or "")
                if lines and previous_prefix == prefix:
                    previous_text = str(state.get("hidden_work_text") or "")
                    if compact_text.startswith("thinking..."):
                        next_text = compact_text
                    elif event.get("snapshot"):
                        next_text = raw_text
                    elif compact_auxiliary_text(previous_text).startswith("thinking..."):
                        next_text = raw_text
                    else:
                        next_text = previous_text + raw_text
                    state["hidden_work_text"] = next_text
                    lines[-1] = f"{prefix}: {compact_auxiliary_text(next_text)}" if compact_auxiliary_text(next_text) else prefix
                else:
                    state["hidden_work_prefix"] = prefix
                    state["hidden_work_text"] = raw_text if not compact_text.startswith("thinking...") else compact_text
                    lines.append(f"{prefix}: {compact_text}" if compact_text else prefix)
            elif event_type in {TOOL_CALL_STARTED, TOOL_CALL_DELTA, TOOL_CALL_FINISHED}:
                state["hidden_work_prefix"] = ""
                state["hidden_work_text"] = ""
                value = str(line).strip()
                if value:
                    lines.append(value)
            else:
                state["hidden_work_prefix"] = ""
                state["hidden_work_text"] = ""
                for part in str(line).splitlines() or [line]:
                    value = part.strip()
                    if value:
                        lines.append(value)
            state["auxiliary_lines"] = lines

        def callback(event: dict[str, Any]) -> None:
            normalized = normalize_yoagent_stream_event(event, backend=backend)
            event_type = str(normalized.get("kind") or normalized.get("event") or "")
            if event_type == ASSISTANT_DELTA:
                incoming_text = str(normalized.get("text") or "")
                if normalized.get("snapshot"):
                    state["raw_content"] = incoming_text
                else:
                    state["raw_content"] = str(state.get("raw_content") or "") + incoming_text
                visible_text, hidden_thinking_removed = strip_yoagent_stream_hidden_thinking(str(state.get("raw_content") or ""))
                hidden_changed = bool(hidden_thinking_removed and not state.get("hidden_thinking_removed"))
                state["hidden_thinking_removed"] = bool(state.get("hidden_thinking_removed") or hidden_thinking_removed)
                if visible_text == state.get("last_content") and not hidden_changed:
                    return
                previous_visible = str(state.get("last_content") or "")
                state["last_content"] = visible_text
                visible_delta = visible_text
                if normalized.get("snapshot"):
                    visible_delta = visible_text
                elif previous_visible and visible_text.startswith(previous_visible):
                    visible_delta = visible_text[len(previous_visible):]
                append_stream_item("assistant", visible_delta)
                publish([normalized], phase="answer" if visible_text else "thinking")
                return
            if event_type == HIDDEN_WORK_DELTA:
                state["hidden_thinking_removed"] = True
                state["hidden_work_active"] = True
                state["auxiliary_done"] = False
                append_auxiliary_line(normalized)
                replace_or_append_stream_item("thinking", hidden_work_stream_item_text(normalized))
                publish([normalized], phase="thinking")
                return
            if event_type in {TOOL_CALL_STARTED, TOOL_CALL_DELTA}:
                state["tool_active"] = True
                state["auxiliary_done"] = False
                append_auxiliary_line(normalized)
                append_stream_item("tool", yoagent_stream_event_auxiliary_line(normalized), merge=False)
                publish([normalized], phase="tool")
                return
            if event_type in {TOOL_CALL_FINISHED, HIDDEN_WORK_DONE}:
                if event_type == TOOL_CALL_FINISHED:
                    state["tool_active"] = False
                if event_type == HIDDEN_WORK_DONE:
                    state["hidden_work_active"] = False
                append_auxiliary_line(normalized)
                if event_type == TOOL_CALL_FINISHED:
                    append_stream_item("tool", yoagent_stream_event_auxiliary_line(normalized), merge=False)
                publish([normalized], phase="tool" if event_type == TOOL_CALL_FINISHED else "thinking")
                return
            if event_type in {TURN_DONE, ERROR}:
                state["hidden_work_active"] = False
                state["tool_active"] = False
                state["auxiliary_done"] = True
                append_auxiliary_line(normalized)
                publish([normalized], phase="done" if event_type == TURN_DONE else "error")
                return
            append_auxiliary_line(normalized)
            publish([normalized], phase=str(normalized.get("native_type") or "event"))

        return callback

    def yoagent_job_prompt_text(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_prompt_text(*args, **kwargs)

    def yoagent_job_transport_id(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_transport_id(*args, **kwargs)

    def yoagent_job_result_marker_from_result(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_result_marker_from_result(*args, **kwargs)

    def yoagent_job_result_source_from_result(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_result_source_from_result(*args, **kwargs)

    def sanitize_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.sanitize_yoagent_job(*args, **kwargs)

    def load_yoagent_jobs(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.load_yoagent_jobs(*args, **kwargs)

    def persist_yoagent_jobs_locked(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.persist_yoagent_jobs_locked(*args, **kwargs)

    def publish_yoagent_jobs_changed(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.publish_yoagent_jobs_changed(*args, **kwargs)

    def public_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.public_yoagent_job(*args, **kwargs)

    def public_yoagent_action_preview(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.public_yoagent_action_preview(*args, **kwargs)

    def yoagent_jobs_payload(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_jobs_payload(*args, **kwargs)

    def yoagent_job_idempotency_key(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_idempotency_key(*args, **kwargs)

    def yoagent_job_spec_from_payload(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_spec_from_payload(*args, **kwargs)

    def create_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.create_yoagent_job(*args, **kwargs)

    def confirm_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.confirm_yoagent_job(*args, **kwargs)

    def cancel_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.cancel_yoagent_job(*args, **kwargs)

    def cancel_yoagent_jobs_for_session(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.cancel_yoagent_jobs_for_session(*args, **kwargs)

    def yoagent_intent(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_intent(*args, **kwargs)

    def yoagent_job_observed_state(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_observed_state(*args, **kwargs)

    def update_yoagent_job_observation(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.update_yoagent_job_observation(*args, **kwargs)

    def complete_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.complete_yoagent_job(*args, **kwargs)

    def fire_yoagent_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.fire_yoagent_job(*args, **kwargs)

    def poll_yoagent_jobs_once(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.poll_yoagent_jobs_once(*args, **kwargs)

    def yoagent_wait_regarding_text(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_wait_regarding_text(*args, **kwargs)

    def yoagent_action_wait_label(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_wait_label(*args, **kwargs)

    def register_yoagent_action_wait(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.register_yoagent_action_wait(*args, **kwargs)

    def finish_yoagent_action_wait(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.finish_yoagent_action_wait(*args, **kwargs)

    def clear_yoagent_action_wait(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.clear_yoagent_action_wait(*args, **kwargs)

    def yoagent_prompt_history(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_prompt_history(*args, **kwargs)

    def yoagent_activity_payload(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_activity_payload(*args, **kwargs)

    def prune_yoagent_action_previews(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.prune_yoagent_action_previews(*args, **kwargs)

    def yoagent_managed_target_metadata(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_managed_target_metadata(*args, **kwargs)

    def yoagent_target_with_transport(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_target_with_transport(*args, **kwargs)

    @requires_known_session(refresh=True)
    def yoagent_action_target(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_target(*args, **kwargs)

    def yoagent_action_pane_status(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_pane_status(*args, **kwargs)

    def yoagent_action_acceptance(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_acceptance(*args, **kwargs)

    def yoagent_action_risk_labels(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_risk_labels(*args, **kwargs)

    def create_yoagent_action_preview(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.create_yoagent_action_preview(*args, **kwargs)

    def yoagent_action_answer(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_answer(*args, **kwargs)

    def yoagent_action_preview_details(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_preview_details(*args, **kwargs)

    def yoagent_action_sent_answer(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_sent_answer(*args, **kwargs)

    def yoagent_job_answer(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_job_answer(*args, **kwargs)

    def yoagent_action_result_marker(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_result_marker(*args, **kwargs)

    def yoagent_transcript_delta_text(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_transcript_delta_text(*args, **kwargs)

    def yoagent_action_result_text_from_transcript_delta(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_result_text_from_transcript_delta(*args, **kwargs)

    def yoagent_action_visible_result_text(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_action_visible_result_text(*args, **kwargs)

    def record_yoagent_action_result(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.record_yoagent_action_result(*args, **kwargs)

    def yoagent_handoff_time_add_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_handoff_time_add_prompt(*args, **kwargs)

    def yoagent_derived_handoff_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_derived_handoff_prompt(*args, **kwargs)

    def yoagent_source_neutral_handoff_instruction(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_source_neutral_handoff_instruction(*args, **kwargs)

    def yoagent_handoff_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_handoff_prompt(*args, **kwargs)

    def continue_yoagent_handoff(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.continue_yoagent_handoff(*args, **kwargs)

    def finish_yoagent_action_result(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.finish_yoagent_action_result(*args, **kwargs)

    def run_yoagent_action_result_watcher(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.run_yoagent_action_result_watcher(*args, **kwargs)

    def start_yoagent_action_result_watcher(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.start_yoagent_action_result_watcher(*args, **kwargs)

    def yoagent_composer_text_is_idle_placeholder(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_composer_text_is_idle_placeholder(*args, **kwargs)

    def yoagent_visible_composer_text(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_visible_composer_text(*args, **kwargs)

    def yoagent_text_still_in_composer(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_text_still_in_composer(*args, **kwargs)

    def yoagent_clear_target_composer(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_clear_target_composer(*args, **kwargs)

    def preview_yoagent_send_action(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.preview_yoagent_send_action(*args, **kwargs)

    def execute_yoagent_send_action(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.execute_yoagent_send_action(*args, **kwargs)

    def reset_yoagent_chat(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.reset_yoagent_chat(*args, **kwargs)

    def start_yoagent_backend_prewarm(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.start_yoagent_backend_prewarm(*args, **kwargs)

    def yoagent_startup_response(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_startup_response(*args, **kwargs)

    def yoagent_prewarm(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_prewarm(*args, **kwargs)

    def yoagent_chat(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_chat(*args, **kwargs)

    def cancel_yoagent_chat(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.cancel_yoagent_chat(*args, **kwargs)

    def set_yoagent_chat_request_interrupt(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.set_yoagent_chat_request_interrupt(*args, **kwargs)

    def yoagent_chat_request_cancel_event(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_chat_request_cancel_event(*args, **kwargs)

    def yoagent_chat_request_cancelled(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_chat_request_cancelled(*args, **kwargs)

    def complete_yoagent_chat_request(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.complete_yoagent_chat_request(*args, **kwargs)

    def interrupt_yoagent_claude_process(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.interrupt_yoagent_claude_process(*args, **kwargs)

    def run_yoagent_cli_backend(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.run_yoagent_cli_backend(*args, **kwargs)

    def run_yoagent_codex_cli(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.run_yoagent_codex_cli(*args, **kwargs)

    def run_yoagent_claude_cli(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.run_yoagent_claude_cli(*args, **kwargs)

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        payload = save_settings(patch)
        self.publish_client_event("settings_changed", {"mtime_ns": payload.get("mtime_ns", 0), "data": payload}, trigger="manual", cache="ready")
        self.client_watch_wake_event.set()
        return payload

    def yolo_rules_payload(self) -> dict[str, Any]:
        return yolo_rules.rules_status()

    def reload_yolo_rules(self) -> dict[str, Any]:
        return yolo_rules.reload_rules()

    def ensure_yolo_rules_file(self) -> dict[str, Any]:
        yolo_rules.ensure_rule_file()
        return yolo_rules.reload_rules()

    def auto_approve_interval_seconds(self) -> float:
        return self.performance_setting_seconds("auto_approve_interval_seconds", 0.1, 10.0)

    def auto_approve_prompt_source(self) -> str:
        value = settings_payload().get("settings", {}).get("yolo", {}).get("prompt_source", "hybrid")
        return value if value in {"pane", "hybrid"} else "hybrid"

    def metadata_badge_pulse_seconds(self) -> float:
        value = settings_payload().get("settings", {}).get("appearance", {}).get("metadata_badge_pulse_seconds", METADATA_BADGE_PULSE_SECONDS)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return METADATA_BADGE_PULSE_SECONDS

    def set_notify(self, enabled: bool) -> dict[str, Any]:
        update_yolomux_state({"notify_enabled": enabled})
        self.log_event(None, "notify_enabled" if enabled else "notify_disabled", "Notify enabled" if enabled else "Notify disabled", {})
        return {"enabled": enabled}

    def log_event(self, session: str | None, event_type: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.event_log.append(session, event_type, message, details)

    def log_auto_event(self, session: str, event_type: str, message: str, details: dict[str, Any]) -> None:
        self.log_event(session, event_type, message, details)

    def events_payload(self, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        return {
            "events": self.event_log.tail(session=session, limit=bounded_limit),
            "session": session or "",
            "limit": bounded_limit,
        }, HTTPStatus.OK

    def search_result_for_summary(
        self,
        *,
        query: str,
        session: str,
        text: str,
        kind: str,
        source: str,
        timestamp: str = "",
        title: str = "",
    ) -> SearchResult | None:
        if not str(query or "").strip() or str(query).strip().lower() not in str(text or "").lower():
            return None
        target_type = "activity-summary" if kind == "global_summary" else "summary"
        return {
            "session": session,
            "timestamp": timestamp,
            "kind": kind,
            "source": source,
            "title": title or (f"{session} summary" if session else "Global summary"),
            "snippet": search_snippet(text, query),
            "target": {
                "type": target_type,
                "session": session,
                "timestamp": timestamp,
                "tab": "summary" if session else "yoagent",
            },
        }

    def search_summary_results(self, query: str, session: str | None, limit: int) -> tuple[list[SearchResult], list[dict[str, Any]]]:
        text = str(query or "").strip()
        if not text:
            return [], []
        search_sessions = [session] if session else self.sessions
        results: list[SearchResult] = []
        legacy_summaries: list[dict[str, Any]] = []
        with getattr(self, "yoagent_session_summary_lock", threading.RLock()):
            rolling_summaries = copy.deepcopy(getattr(self, "yoagent_session_summaries", {}))
        for name in search_sessions:
            if len(results) >= limit:
                break
            summary, status = self.summary(name)
            summary_text = summary.get("text") if status == HTTPStatus.OK else ""
            if isinstance(summary_text, str):
                result = self.search_result_for_summary(
                    query=text,
                    session=name or "",
                    text=summary_text,
                    kind="summary",
                    source="session_summary",
                    title=f"{name} summary",
                )
                if result:
                    results.append(result)
                    legacy_summaries.append({"session": name, "type": "summary", "text": truncate_text(summary_text, 2000)})
            rolling = rolling_summaries.get(name) if isinstance(rolling_summaries, dict) else None
            rolling_text = rolling.get("rolling_summary") if isinstance(rolling, dict) else ""
            if isinstance(rolling_text, str):
                result = self.search_result_for_summary(
                    query=text,
                    session=name or "",
                    text=rolling_text,
                    kind="summary",
                    source="rolling_summary",
                    timestamp=utc_iso_from_ts(rolling.get("updated_ts") if isinstance(rolling, dict) else 0),
                    title=f"{name} rolling summary",
                )
                if result and len(results) < limit:
                    results.append(result)
                    legacy_summaries.append({"session": name, "type": "rolling_summary", "text": truncate_text(rolling_text, 2000)})
        if not session and len(results) < limit and hasattr(self, "activity_summary_lock"):
            activity_payload = self.activity_summary_payload()
            global_payload = activity_payload.get("global") if isinstance(activity_payload, dict) else None
            lines = global_payload.get("lines") if isinstance(global_payload, dict) else None
            global_text = "\n".join(str(line) for line in lines if isinstance(line, str)) if isinstance(lines, list) else ""
            result = self.search_result_for_summary(
                query=text,
                session="",
                text=global_text,
                kind="global_summary",
                source="global_summary",
                timestamp=str(activity_payload.get("generated_at") or ""),
                title="Global activity summary",
            )
            if result:
                results.append(result)
                legacy_summaries.append({"session": "", "type": "global_summary", "text": truncate_text(global_text, 2000)})
        return results[:limit], legacy_summaries[:limit]

    def search_payload(self, query: str, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        text = str(query or "").strip()
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        event_matches = self.event_log.search(text, session=session, limit=bounded_limit)
        event_results = self.event_log.search_results(text, session=session, limit=bounded_limit)
        summary_results, summary_matches = self.search_summary_results(text, session, max(0, bounded_limit - len(event_results)))
        results = [*event_results, *summary_results]
        return {
            "query": text,
            "session": session or "",
            "limit": bounded_limit,
            "strategy": "scan-on-query",
            "sources": {
                "events": str(self.event_log.path),
                "summaries": ["session summaries", "rolling per-session summaries", "global activity summary"],
            },
            "result_shape": ["session", "timestamp", "kind", "snippet", "target"],
            "results": results,
            "events": event_matches,
            "summaries": summary_matches,
        }, HTTPStatus.OK

    def active_window_for(self, session: str) -> str | None:
        """Cheap active-window lookup from the cached transcripts payload (no tmux spawn).
        Returns the active window index for ``session`` or None when unknown."""
        cached = self.get_transcripts_payload_cache(max_age_seconds=float("inf"), allow_stale=True)
        if not cached:
            return None
        payload = cached[0]
        info = (payload.get("sessions") or {}).get(session) if isinstance(payload, dict) else None
        panes = info.get("panes") if isinstance(info, dict) else None
        if not isinstance(panes, list):
            return None
        return active_window_for_panes(panes)

    def record_user_input(self, session: str, byte_count: int, source: str = "host") -> None:
        """One user-input heartbeat from the WS bridge. Read-only viewers are dropped upstream;
        write-share input passes source="share" so the heartbeat log can distinguish it."""
        if not session:
            return
        self.activity_ledger.heartbeat(session, self.active_window_for(session), byte_count=byte_count, source=source)

    def build_activity_payload(self, session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        session_names, scope_errors, scope = self.activity_session_names(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        sessions, errors = discover_sessions(session_names)
        errors = [*scope_errors, *errors]
        ordered_sessions = self.tmux_recency_ordered_sessions(session_names)
        agent_infos = {session: sessions[session] for session in ordered_sessions if session in sessions and sessions[session].agents}
        session_files_by_session = self.cached_session_files_payloads_for_infos(agent_infos, hours=bounded_hours)
        activity_snapshot = self.activity_snapshot_with_recency()
        return {
            "activity": activity_snapshot,
            "agents": build_recent_agents_payload(sessions, ordered_sessions, session_files_by_session=session_files_by_session),
            "errors": errors,
            "session_scope": scope,
            "session_file_hours": bounded_hours,
        }

    def set_tabber_activity_cache(self, payload: dict[str, Any]) -> None:
        with self.tabber_activity_cache_lock:
            self.tabber_activity_cache = (time.monotonic(), copy.deepcopy(payload))

    def get_tabber_activity_cache(self, max_age_seconds: float, allow_stale: bool = True) -> tuple[dict[str, Any], bool, float] | None:
        now = time.monotonic()
        with self.tabber_activity_cache_lock:
            cached = self.tabber_activity_cache
            if cached is None:
                return None
            stored_at, payload = cached
            age_seconds = max(0.0, now - stored_at)
            fresh = age_seconds <= max_age_seconds
            if not fresh and not allow_stale:
                return None
            return copy.deepcopy(payload), fresh, age_seconds

    def refresh_tabber_activity_cache(self) -> dict[str, Any]:
        payload = self.build_activity_payload()
        self.set_tabber_activity_cache(payload)
        return payload

    def run_tabber_activity_cache_refresh(self) -> None:
        try:
            self.refresh_tabber_activity_cache()
        finally:
            with self.tabber_activity_cache_lock:
                self.tabber_activity_cache_refreshing = False

    def start_tabber_activity_cache_refresh(self) -> bool:
        with self.tabber_activity_cache_lock:
            if self.tabber_activity_cache_refreshing:
                return False
            self.tabber_activity_cache_refreshing = True
        worker = threading.Thread(target=self.run_tabber_activity_cache_refresh, name="tabber-activity-refresh", daemon=True)
        worker.start()
        return True

    def start_tabber_activity_cache_warmer(self) -> bool:
        with self.tabber_activity_cache_lock:
            if self.tabber_activity_cache_warmer_running:
                return False
            self.tabber_activity_cache_warmer_running = True
        worker = threading.Thread(target=self.tabber_activity_cache_warmer_loop, name="tabber-activity-cache", daemon=True)
        self.tabber_activity_cache_warmer_thread = worker
        worker.start()
        return True

    def tabber_activity_cache_warmer_loop(self) -> None:
        try:
            while True:
                started = time.monotonic()
                try:
                    self.refresh_tabber_activity_cache()
                except (OSError, RuntimeError, ValueError) as exc:
                    self.log_event(None, "client_event_watch_error", f"Tabber activity cache refresh failed: {exc}", {})
                interval = self.tabber_activity_refresh_seconds()
                elapsed = max(0.0, time.monotonic() - started)
                time.sleep(max(0.1, interval - elapsed))
        finally:
            with self.tabber_activity_cache_lock:
                self.tabber_activity_cache_warmer_running = False

    def activity_payload(self) -> tuple[dict[str, Any], HTTPStatus]:
        refresh_seconds = self.tabber_activity_refresh_seconds()
        cached = self.get_tabber_activity_cache(refresh_seconds, allow_stale=True)
        if cached:
            payload, fresh, age_seconds = cached
            payload["cache"] = {
                "hit": True,
                "stale": not fresh,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": refresh_seconds,
            }
            if not fresh:
                payload["cache"]["refreshing"] = self.start_tabber_activity_cache_refresh()
            return payload, HTTPStatus.OK
        payload = self.build_activity_payload()
        self.set_tabber_activity_cache(payload)
        payload = copy.deepcopy(payload)
        payload["cache"] = {
            "hit": False,
            "stale": False,
            "age_seconds": 0,
            "refresh_seconds": refresh_seconds,
            "refreshing": False,
        }
        return payload, HTTPStatus.OK

    def run_history_store_for_app(self) -> RunHistoryStore:
        store = getattr(self, "run_history_store", None)
        if isinstance(store, RunHistoryStore):
            return store
        store = RunHistoryStore(RUN_HISTORY_PATH)
        self.run_history_store = store
        return store

    def yoagent_session_summary_record(self, session: str) -> dict[str, Any]:
        lock = getattr(self, "yoagent_session_summary_lock", threading.RLock())
        with lock:
            summaries = getattr(self, "yoagent_session_summaries", {})
            value = summaries.get(session) if isinstance(summaries, dict) else None
            return copy.deepcopy(value) if isinstance(value, dict) else {}

    def latest_summary_for_run_history(self, session: str) -> tuple[str, float]:
        rolling = self.yoagent_session_summary_record(session)
        rolling_text = str(rolling.get("rolling_summary") or "").strip()
        if rolling_text:
            return redacted_action_text(rolling_text, 1200), self.float_value(rolling.get("updated_ts"), 0.0)
        if hasattr(self, "transcript_tail_cache_lock"):
            try:
                payload, status = self.summary(session)
            except (AttributeError, OSError, RuntimeError, ValueError):
                payload, status = {}, HTTPStatus.INTERNAL_SERVER_ERROR
            summary_text = payload.get("text") if status == HTTPStatus.OK and isinstance(payload, dict) else ""
            if isinstance(summary_text, str) and summary_text.strip():
                return redacted_action_text(summary_text, 1200), 0.0
        return "", 0.0

    def compact_agent_for_run_history(self, agent: Any) -> dict[str, Any] | None:
        if agent is None:
            return None
        return {
            "kind": agent.kind,
            "model": agent.model or "",
            "session_id": agent.session_id or "",
            "pid": agent.pid,
            "status": agent.status or "",
            "transcript": agent.transcript or "",
            "pane_target": agent.pane_target,
        }

    def run_history_id(self, session: str, agent: Any, selected: Any) -> str:
        if agent is not None:
            if agent.session_id:
                return f"{agent.kind}:{agent.session_id}"
            if agent.transcript:
                return f"{agent.kind}:{agent.transcript}"
            return f"{agent.kind}:{session}:{agent.pid}"
        target = selected.target if selected is not None else ""
        return f"tmux:{session}:{target}"

    def run_history_entry_for_session(self, session: str, info: SessionInfo) -> RunHistoryEntry:
        selected = info.selected_pane
        agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        project = session_project_metadata(info, self.metadata_cache, allow_network=False)
        transcript = agent.transcript if agent and agent.transcript else ""
        transcript_mtime = session_files.file_mtime(Path(transcript)) if transcript else 0.0
        transcript_meta = transcript_run_metadata(transcript, agent.kind if agent else "")
        if not transcript_meta.get("ended_ts") and transcript_mtime:
            transcript_meta["ended_ts"] = transcript_mtime
            transcript_meta["ended_at"] = utc_iso_from_ts(transcript_mtime)
        if not transcript_meta.get("started_ts") and transcript_mtime:
            transcript_meta["started_ts"] = transcript_mtime
            transcript_meta["started_at"] = utc_iso_from_ts(transcript_mtime)
        latest_summary, latest_summary_updated_ts = self.latest_summary_for_run_history(session)
        rolling = self.yoagent_session_summary_record(session)
        rolling_state = str(rolling.get("state") or "").strip().lower()
        final_state = rolling_state if rolling_state in YOAGENT_SESSION_SUMMARY_STATES else str(transcript_meta.get("final_state") or "idle")
        if agent and agent.status == "running":
            final_state = "working"
        pull_request = compact_pull_request_for_history(project.get("pull_request") if isinstance(project, dict) else None)
        return {
            "id": self.run_history_id(session, agent, selected),
            "session": session,
            "agent": self.compact_agent_for_run_history(agent),
            "prompt": redacted_action_text(str(transcript_meta.get("prompt") or ""), 1200),
            "cwd": agent.cwd if agent and agent.cwd else selected.current_path if selected else "",
            "tmux_target": selected.target if selected else "",
            "tmux_command": selected.process_label or selected.command if selected else "",
            "started_at": str(transcript_meta.get("started_at") or ""),
            "started_ts": self.float_value(transcript_meta.get("started_ts"), 0.0),
            "ended_at": str(transcript_meta.get("ended_at") or ""),
            "ended_ts": self.float_value(transcript_meta.get("ended_ts"), 0.0),
            "final_state": final_state,
            "pr": pull_request,
            "latest_summary": latest_summary,
            "latest_summary_updated_ts": latest_summary_updated_ts,
            "transcript": transcript,
            "transcript_mtime": transcript_mtime,
            "project": project,
            "recent_events": self.event_log.tail(session=session, limit=5),
        }

    def run_history_payload(self, session: str | None = None) -> tuple[RunHistoryPayload, HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        store = self.run_history_store_for_app()
        stored_before = store.load_rows(session=session)
        if session and session not in self.sessions and not stored_before:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        scope = [session] if session and session in self.sessions else ([] if session else self.sessions)
        infos, errors = discover_sessions(scope)
        runs: list[RunHistoryEntry] = []
        for name in scope:
            info = infos.get(name)
            if info is None:
                continue
            runs.append(self.run_history_entry_for_session(name, info))
        if runs:
            store.upsert_rows(runs)
        rows = store.load_rows(session=session)
        return {"session": session or "", "runs": rows, "errors": [*refresh_errors, *errors]}, HTTPStatus.OK

    def session_files_payload(
        self,
        session: str | None = None,
        hours: float = 24.0,
        from_ref: str | None = None,
        to_ref: str | None = None,
        repo_refs: dict[str, dict[str, str]] | None = None,
        force: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}", "session": session}, HTTPStatus.NOT_FOUND
        scope = [session] if session else self.sessions
        infos, errors = discover_sessions(scope)
        return self.session_files_payload_for_infos(
            session,
            infos,
            hours,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            force=force,
            extra_errors=[*refresh_errors, *errors],
        )

    def session_files_batch_payload(
        self,
        sessions: list[str] | None = None,
        hours: float = 24.0,
        from_ref: str | None = None,
        to_ref: str | None = None,
        repo_refs: dict[str, dict[str, str]] | None = None,
        force: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        requested: list[str] = []
        seen: set[str] = set()
        for raw_session in sessions or self.sessions:
            session = str(raw_session or "").strip()
            if not session or session in seen:
                continue
            seen.add(session)
            requested.append(session)
        invalid = [session for session in requested if session not in self.sessions]
        valid = [session for session in requested if session in self.sessions]
        infos, errors = discover_sessions(valid)
        payloads: dict[str, dict[str, Any]] = {}
        statuses: dict[str, int] = {}
        batch_infos: dict[str, SessionInfo] = {}
        for session in requested:
            if session in invalid:
                payloads[session] = {"error": f"unknown session: {session}", "session": session, "errors": []}
                statuses[session] = int(HTTPStatus.NOT_FOUND)
                continue
            info = infos.get(session)
            if info is None:
                payloads[session] = {"error": f"session unavailable: {session}", "session": session, "errors": []}
                statuses[session] = int(HTTPStatus.NOT_FOUND)
                continue
            batch_infos[session] = info

        def load_session_payload(name: str, info: SessionInfo) -> tuple[dict[str, Any], HTTPStatus]:
            return self.session_files_payload_for_infos(
                name,
                {name: info},
                hours,
                from_ref=from_ref,
                to_ref=to_ref,
                repo_refs=repo_refs,
                force=force,
            )

        if len(batch_infos) == 1:
            session, info = next(iter(batch_infos.items()))
            payload, status = load_session_payload(session, info)
            payloads[session] = payload
            statuses[session] = int(status)
        elif batch_infos:
            with ThreadPoolExecutor(max_workers=session_files_batch_worker_count(len(batch_infos)), thread_name_prefix="session-files-batch") as executor:
                futures = {executor.submit(load_session_payload, session, info): session for session, info in batch_infos.items()}
                for future in as_completed(futures):
                    session = futures[future]
                    payload, status = future.result()
                    payloads[session] = payload
                    statuses[session] = int(status)
        return {
            "sessions": payloads,
            "statuses": statuses,
            "errors": [*refresh_errors, *errors],
        }, HTTPStatus.OK

    def client_event(self, event: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        session = event.get("session")
        if session is not None and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        event_type = event.get("type")
        message = event.get("message")
        if not isinstance(event_type, str) or not event_type:
            return {"error": "missing event type"}, HTTPStatus.BAD_REQUEST
        if not isinstance(message, str) or not message:
            return {"error": "missing event message"}, HTTPStatus.BAD_REQUEST
        details = event.get("details")
        if not isinstance(details, dict):
            details = {}
        saved = self.log_event(session, event_type, message, details)
        return {"ok": True, "event": saved}, HTTPStatus.OK

    def restore_auto_approve(self) -> list[str]:
        restored: list[str] = []
        for session in self.persisted_auto_sessions():
            payload, status = self.set_auto_approve(session, True, persist=False, takeover=False)
            if status == HTTPStatus.OK and payload.get("enabled") is True:
                restored.append(session)
        return restored

    def handle_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "disable_auto_approve":
            session = request.get("session")
            requester = request.get("requester")
            return self.disable_auto_approve_for_takeover(session, requester if isinstance(requester, dict) else {})
        return {"ok": False, "error": f"unknown action: {action}"}

    def disable_auto_approve_for_takeover(self, session: Any, requester: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(session, str) or session not in self.sessions:
            return {"ok": False, "error": f"unknown session: {session}"}
        with self.auto_workers_lock:
            worker_sessions = self.auto_worker_session_map()
            workers = [
                (key, worker)
                for key, worker in self.auto_workers.items()
                if worker_sessions.get(key, key) == session
            ]
            if not workers:
                return {"ok": True, "session": session, "enabled": False, "message": "YOLO was not enabled here"}
            # confirm the worker thread actually exited (and released its flock) BEFORE
            # reporting released — otherwise the requester could re-acquire while this worker is still
            # alive and about to fire one more keystroke (two workers on one session).
            released = True
            for key, worker in workers:
                released = worker.stop() and released
                self.auto_workers.pop(key, None)
                worker_sessions.pop(key, None)
        if not released:
            self.log_event(session, "yolo_release_timeout", "YOLO worker did not stop in time", {"requester": requester})
            return {"ok": False, "session": session, "error": "YOLO worker did not stop in time"}
        self.log_event(session, "yolo_released", "YOLO released for another server", {"requester": requester})
        return {"ok": True, "session": session, "enabled": False}

    def build_transcripts_payload(self) -> dict[str, Any]:
        refresh_errors = self.refresh_sessions()
        sessions, errors = discover_sessions(self.sessions)
        session_payloads = {
            name: session_to_json(info, self.metadata_cache, allow_network=False)
            for name, info in sessions.items()
        }
        payload = {
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "server_version": YOLOMUX_VERSION,
            "server_started_at": SERVER_STARTED_AT,
            "server_uptime_seconds": max(0.0, time.time() - SERVER_STARTED_AT),
            "session_order": self.sessions,
            "sessions": session_payloads,
            # refresh agent login status on the metadata poll (cached server-side) so the
            # new-session picker re-enables an agent within the cache TTL after the user logs in.
            "agentAuth": agent_auth_status(),
            "errors": [*refresh_errors, *errors],
        }
        self.apply_metadata_badge_pulses(session_payloads)
        self.warm_metadata_cache_async(sessions)
        return payload

    def transcripts_payload(self, force: bool = False) -> dict[str, Any]:
        max_age = TRANSCRIPTS_PAYLOAD_CACHE_SECONDS
        cached = None if force else self.get_transcripts_payload_cache(max_age, allow_stale=True)
        if cached:
            payload, fresh, age_seconds = cached
            payload["cache"] = {
                "hit": True,
                "stale": not fresh,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": max_age,
            }
            if not fresh:
                payload["cache"]["refreshing"] = self.start_transcripts_payload_refresh()
            return payload
        payload = self.build_transcripts_payload()
        self.set_transcripts_payload_cache(payload)
        payload["cache"] = {
            "hit": False,
            "stale": False,
            "age_seconds": 0,
            "refresh_seconds": max_age,
            "refreshing": False,
        }
        return payload

    def apply_metadata_badge_pulses(self, session_payloads: dict[str, dict[str, Any]]) -> None:
        now = time.time()
        next_signatures = {
            session: self.metadata_badge_signatures_for_session(payload)
            for session, payload in session_payloads.items()
        }
        with self.metadata_badge_lock:
            signatures_changed = False
            for session, next_signature in list(next_signatures.items()):
                previous_signature = self.metadata_badge_signatures.get(session)
                if previous_signature and self.metadata_badge_change_is_cold_cache_degradation(previous_signature, next_signature):
                    next_signatures[session] = previous_signature

            for session, badge_times in list(self.metadata_badge_pulse_until.items()):
                current = {badge: until for badge, until in badge_times.items() if until > now}
                if current:
                    self.metadata_badge_pulse_until[session] = current
                else:
                    self.metadata_badge_pulse_until.pop(session, None)

            for session, next_signature in next_signatures.items():
                previous_signature = self.metadata_badge_signatures.get(session)
                if previous_signature is None:
                    continue
                for badge in METADATA_BADGES:
                    if self.metadata_badge_change_should_pulse(previous_signature, next_signature, badge):
                        self.metadata_badge_pulse_until.setdefault(session, {})[badge] = now + self.metadata_badge_pulse_seconds()

            if self.metadata_badge_signatures != next_signatures:
                self.metadata_badge_signatures = next_signatures
                signatures_changed = True

            for session, payload in session_payloads.items():
                badge_times = self.metadata_badge_pulse_until.get(session, {})
                remaining = {
                    badge: max(1, int((until - now) * 1000))
                    for badge, until in badge_times.items()
                    if until > now
                }
                if remaining:
                    payload["metadata_badge_pulse_remaining_ms"] = remaining

            if signatures_changed:
                self.persist_metadata_badge_state_locked()

    def load_metadata_badge_state(self) -> None:
        state = read_yolomux_state()
        with self.metadata_badge_lock:
            self.metadata_badge_signatures = self.sanitized_metadata_badge_signatures(
                state.get(METADATA_BADGE_SIGNATURES_STATE_KEY)
            )
            self.metadata_badge_pulse_until = {}

    def persist_metadata_badge_state_locked(self) -> None:
        update_yolomux_state(
            {
                METADATA_BADGE_SIGNATURES_STATE_KEY: self.metadata_badge_signatures,
                METADATA_BADGE_PULSE_UNTIL_STATE_KEY: {},
            }
        )

    def sanitized_metadata_badge_signatures(self, value: Any) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        clean: dict[str, dict[str, str]] = {}
        for session, badges in value.items():
            if not isinstance(session, str) or not isinstance(badges, dict):
                continue
            clean[session] = {badge: str(badges.get(badge) or "") for badge in METADATA_BADGES}
        return clean

    def sanitized_metadata_badge_pulse_until(self, value: Any) -> dict[str, dict[str, float]]:
        if not isinstance(value, dict):
            return {}
        clean: dict[str, dict[str, float]] = {}
        for session, badges in value.items():
            if not isinstance(session, str) or not isinstance(badges, dict):
                continue
            clean_badges: dict[str, float] = {}
            for badge, pulse_until in badges.items():
                if badge not in METADATA_BADGES or not isinstance(pulse_until, (int, float)):
                    continue
                if pulse_until > 0:
                    clean_badges[badge] = float(pulse_until)
            if clean_badges:
                clean[session] = clean_badges
        return clean

    def metadata_badge_signatures_for_session(self, payload: dict[str, Any]) -> dict[str, str]:
        project = as_dict(payload.get("project"))
        git_data = as_dict(project.get("git"))
        pr = self.metadata_badge_pull_request(project)
        checks = as_dict(pr.get("checks"))
        status = "" if not pr or pr.get("source_only") else self.metadata_badge_status_state(pr)
        check_state = self.metadata_badge_ci_state(checks)
        return {
            "main": "main" if str(git_data.get("branch") or "") in {"main", "master"} else "",
            "pr": str(pr.get("number") or "") if pr else "",
            "status": status,
            "ci": check_state if pr and check_state and check_state != "unknown" else "",
        }

    def metadata_badge_change_should_pulse(self, previous: dict[str, str], next_signature: dict[str, str], badge: str) -> bool:
        previous_value = previous.get(badge, "")
        next_value = next_signature.get(badge, "")
        if previous_value == next_value:
            return False
        if self.metadata_badge_change_is_initial_enrichment(previous, next_signature, badge):
            return False
        if badge == "ci":
            return previous_value in {"", "unknown", "pending", "running"} and next_value in {"passing", "failing"}
        if badge == "status":
            return previous_value in {"open", "draft"} and next_value in {"merged", "closed"}
        if badge == "main":
            return bool(next_value)
        return False

    def metadata_badge_status_state(self, pr: dict[str, Any]) -> str:
        if pr.get("draft") is True:
            return "draft"
        if pr.get("merged") is True or isinstance(pr.get("merged_at"), str):
            return "merged"
        state = pr.get("state")
        if state == "closed":
            return "closed"
        if state == "open":
            return "open"
        return state if isinstance(state, str) and state else "unknown"

    def metadata_badge_ci_state(self, checks: dict[str, Any]) -> str:
        state = str(checks.get("state") or "").strip().lower()
        if state == "success":
            return "passing"
        if state == "failure":
            return "failing"
        return state

    def metadata_badge_change_is_initial_enrichment(self, previous: dict[str, str], next_signature: dict[str, str], badge: str) -> bool:
        previous_pr = previous.get("pr", "")
        next_pr = next_signature.get("pr", "")
        previous_status = previous.get("status", "")
        previous_ci = previous.get("ci", "")
        if previous_status not in {"", "unknown"} or previous_ci:
            return False
        if badge in {"status", "ci"} and previous_pr and previous_pr == next_pr:
            return True
        if badge == "pr" and not previous_pr and next_pr:
            return True
        return False

    def metadata_badge_change_is_cold_cache_degradation(self, previous: dict[str, str], next_signature: dict[str, str]) -> bool:
        previous_pr = previous.get("pr", "")
        next_pr = next_signature.get("pr", "")
        if not previous_pr or previous_pr != next_pr:
            return False
        previous_status = previous.get("status", "")
        next_status = next_signature.get("status", "")
        if previous_status not in {"", "unknown"} and next_status in {"", "unknown"}:
            return True
        return bool(previous.get("ci", "")) and not next_signature.get("ci", "") and next_status in {"", "unknown"}

    def metadata_badge_pull_request(self, project: dict[str, Any]) -> dict[str, Any]:
        pr = project.get("pull_request")
        if isinstance(pr, dict) and pr.get("number"):
            return pr
        git_data = as_dict(project.get("git"))
        if str(git_data.get("branch") or "") not in {"main", "master"}:
            return {}
        number = pull_request_number_from_subject(str(git_data.get("head") or ""))
        if number is None:
            return {}
        return {
            "number": number,
            "checks": github_checks_unknown(),
            "source_only": True,
        }

    def warm_metadata_cache_async(self, sessions: dict[str, SessionInfo]) -> None:
        with self.metadata_warm_lock:
            if self.metadata_warm_running:
                return
            self.metadata_warm_running = True
        snapshot = dict(sessions)
        worker = threading.Thread(target=self.warm_metadata_cache, args=(snapshot,), daemon=True)
        worker.start()

    def warm_metadata_cache(self, sessions: dict[str, SessionInfo]) -> None:
        try:
            for info in sessions.values():
                session_project_metadata(info, self.metadata_cache, allow_network=True)
        finally:
            with self.metadata_warm_lock:
                self.metadata_warm_running = False

    @requires_known_session()
    def tmux_snapshot(self, session: str, lines: int) -> tuple[dict[str, Any], HTTPStatus]:
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        target = info.selected_pane.target if info and info.selected_pane else session
        history_signature = self.tmux_snapshot_history_signature(target)
        safe_lines = self.tmux_snapshot_capture_lines(lines, history_signature)
        cache_key = (session, target, safe_lines)
        if history_signature is not None:
            with self.tmux_snapshot_history_lock:
                previous_signature = self.tmux_snapshot_history_signatures.get(cache_key)
                if previous_signature == history_signature:
                    return {
                        "session": session,
                        "target": target,
                        "text": "",
                        "lines": safe_lines,
                        "unchanged": True,
                        "history_size": history_signature[0],
                        "history_bytes": history_signature[1],
                        "errors": errors,
                    }, HTTPStatus.OK
        # -J rejoins tmux-wrapped lines so a wrapped command is captured as one logical line.
        result = tmux(["capture-pane", "-t", target, "-p", "-J", "-S", f"-{safe_lines}"], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux capture-pane failed")
            return {"session": session, "target": target, "errors": [*errors, error]}, HTTPStatus.INTERNAL_SERVER_ERROR
        if history_signature is not None:
            with self.tmux_snapshot_history_lock:
                self.cache_set_limited(self.tmux_snapshot_history_signatures, cache_key, history_signature, TRANSCRIPT_TAIL_CACHE_MAX_ITEMS)
        return {
            "session": session,
            "target": target,
            "lines": safe_lines,
            "text": result.stdout.rstrip("\n"),
            "unchanged": False,
            "history_size": history_signature[0] if history_signature is not None else None,
            "history_bytes": history_signature[1] if history_signature is not None else None,
            "errors": errors,
        }, HTTPStatus.OK

    @requires_known_session()
    def transcript_tail(self, session: str, lines: int) -> tuple[dict[str, Any], HTTPStatus]:
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        if not info or not info.agents:
            return {"session": session, "errors": errors, "error": "no agent transcript found"}, HTTPStatus.NOT_FOUND
        agent = next((item for item in info.agents if item.transcript), info.agents[0])
        if not agent.transcript:
            return {"session": session, "agent": asdict(agent), "errors": errors, "error": agent.error}, HTTPStatus.NOT_FOUND
        path = Path(agent.transcript)
        safe_lines = min(max(1, lines), MAX_TRANSCRIPT_TAIL_LINES)
        try:
            stat_signature = file_stat_signature(path)
        except OSError as exc:
            return {"session": session, "agent": asdict(agent), "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR
        cache_key = (
            session,
            safe_lines,
            stat_signature,
            agent.kind or "",
            agent.session_id or "",
            agent.status or "",
        )
        with self.transcript_tail_cache_lock:
            cached_text = self.transcript_tail_cache.get(cache_key)
            text = cached_text[1] if cached_text else None
        if text is None:
            try:
                text = tail_file_lines(path, safe_lines)
            except OSError as exc:
                return {"session": session, "agent": asdict(agent), "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR
            with self.transcript_tail_cache_lock:
                self.cache_set_limited(self.transcript_tail_cache, cache_key, (time.monotonic(), text), TRANSCRIPT_TAIL_CACHE_MAX_ITEMS)
        return {
            "session": session,
            "agent": asdict(agent),
            "path": str(path),
            "lines": safe_lines,
            "text": text,
            "errors": errors,
        }, HTTPStatus.OK

    def context_tail(self, session: str, messages: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            return payload, status
        path = payload.get("path")
        text = payload.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            return {"session": session, "error": "missing transcript text"}, HTTPStatus.NOT_FOUND
        lines = compact_transcript_lines(text, max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS)))
        return {
            "session": session,
            "path": path,
            "messages": messages,
            "text": "\n\n".join(lines),
            "agent": payload.get("agent"),
            "errors": payload.get("errors", []),
        }, HTTPStatus.OK

    def context_items(self, session: str, messages: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            return payload, status
        path = payload.get("path")
        text = payload.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            return {"session": session, "error": "missing transcript text"}, HTTPStatus.NOT_FOUND
        safe_messages = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        try:
            stat_signature = file_stat_signature(Path(path))
        except OSError:
            stat_signature = (path, 0, 0)
        cache_key = (session, safe_messages, stat_signature)
        with self.context_items_cache_lock:
            cached_items = self.context_items_cache.get(cache_key)
            items = copy.deepcopy(cached_items[1]) if cached_items else None
        if items is None:
            items = compact_transcript_items(text, safe_messages)
            with self.context_items_cache_lock:
                self.cache_set_limited(
                    self.context_items_cache,
                    cache_key,
                    (time.monotonic(), copy.deepcopy(items)),
                    CONTEXT_ITEMS_CACHE_MAX_ITEMS,
                )
        return {
            "session": session,
            "path": path,
            "messages": safe_messages,
            "items": items,
            "agent": payload.get("agent"),
            "errors": payload.get("errors", []),
        }, HTTPStatus.OK

    def codex_summary_prompt(self, session: str, lookback_seconds: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            return payload, status
        path = payload.get("path")
        text = payload.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            return {"session": session, "error": "missing transcript text"}, HTTPStatus.NOT_FOUND

        bounded_lookback = max(60, min(lookback_seconds, 24 * 3600))
        since = datetime.now(timezone.utc) - timedelta(seconds=bounded_lookback)
        items, stats = compact_transcript_items_since(text, since)
        fallback = False
        if not items:
            fallback = True
            items = compact_transcript_items(text, MAX_COMPACT_TRANSCRIPT_ITEMS)

        summary_text = "\n\n".join(format_transcript_item(item) for item in items)
        summary_text, truncated = trim_prompt_text(summary_text, SUMMARY_MAX_PROMPT_CHARS)
        sessions, discovery_errors = discover_sessions(self.sessions)
        focus_root, inventory = project_inventory(sessions, session)
        prompt = codex_summary_prompt(
            session=session,
            transcript_path=path,
            transcript_text=summary_text,
            focus_root=focus_root,
            project_inventory=inventory,
            since=since,
            lookback_seconds=bounded_lookback,
            fallback=fallback,
            truncated=truncated,
            stats=stats,
        )
        return {
            "session": session,
            "path": path,
            "prompt": prompt,
            "since": since.isoformat(),
            "lookback_seconds": bounded_lookback,
            "items": len(items),
            "fallback": fallback,
            "truncated": truncated,
            "stats": stats,
            "focus_root": focus_root,
            "projects": inventory,
            "agent": payload.get("agent"),
            "errors": [*payload.get("errors", []), *discovery_errors],
        }, HTTPStatus.OK

    @requires_known_session()
    def summary(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        selected = info.selected_pane if info else None
        agent = next((item for item in info.agents if item.transcript), None) if info else None
        if agent is None and info and info.agents:
            agent = info.agents[0]

        lines: list[str] = [f"tmux session: {session}"]
        if selected:
            lines.append(f"active target: {selected.target}")
            lines.append(f"pane: {selected.command} in {selected.current_path}")
            if selected.title:
                lines.append(f"title: {selected.title}")
        else:
            lines.append("active target: not found")
        if agent:
            lines.append(f"agent: {agent.kind} pid={agent.pid} status={agent.status or 'unknown'}")
            if agent.transcript:
                lines.append(f"transcript: {agent.transcript}")
            elif agent.error:
                lines.append(f"transcript: {agent.error}")

        snapshot, snapshot_status = self.tmux_snapshot(session, 12)
        if snapshot_status == HTTPStatus.OK and isinstance(snapshot.get("text"), str):
            visible = [line for line in snapshot["text"].splitlines() if line.strip()]
            if visible:
                lines.append("")
                lines.append("visible terminal tail:")
                lines.extend(f"- {truncate_text(line, 220)}" for line in visible[-6:])

        context, context_status = self.context_tail(session, 8)
        if context_status == HTTPStatus.OK and isinstance(context.get("text"), str):
            recent = compact_summary_lines(context["text"])
            if recent:
                lines.append("")
                lines.append("recent transcript activity:")
                lines.extend(f"- {line}" for line in recent[-8:])
        recent_events = self.event_log.tail(session=session, limit=5)
        if recent_events:
            lines.append("")
            lines.append("recent events:")
            for event in recent_events[-5:]:
                event_time = event.get("time", "")
                event_type = event.get("type", "")
                message = event.get("message", "")
                lines.append(f"- {event_time} {event_type}: {message}".strip())
        if errors:
            lines.append("")
            lines.append("discovery warnings:")
            lines.extend(f"- {error}" for error in errors)
        return {
            "session": session,
            "text": "\n".join(lines),
            "errors": errors,
        }, HTTPStatus.OK

    @requires_known_session()
    def tmux_next_window(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        result = tmux(["next-window", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux next-window failed")
            return {"session": session, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {"session": session, "ok": True}, HTTPStatus.OK

    @requires_known_session()
    def tmux_select_window(self, session: str, window: str) -> tuple[dict[str, Any], HTTPStatus]:
        window_text = str(window or "").strip()
        if not window_text.isdigit():
            return {"session": session, "error": "window must be a non-negative integer"}, HTTPStatus.BAD_REQUEST
        target = f"{tmux_session_target(session)}{window_text}"
        result = tmux(["select-window", "-t", target], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux select-window failed").strip()
            return {"session": session, "window": window_text, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {"session": session, "window": window_text, "ok": True}, HTTPStatus.OK

    def stop_auto_approve_worker(self, session: str) -> None:
        with self.auto_workers_lock:
            worker_sessions = self.auto_worker_session_map()
            workers = [
                self.auto_workers.pop(key)
                for key in self.auto_approve_worker_keys_for_session_locked(session)
                if key in self.auto_workers
            ]
            for key, owner_session in list(worker_sessions.items()):
                if owner_session == session:
                    worker_sessions.pop(key, None)
        for worker in workers:
            worker.stop()
        self.set_persisted_auto_session(session, False)

    @requires_known_session(refresh=True)
    def rename_session(self, session: str, new_name: str) -> tuple[dict[str, Any], HTTPStatus]:
        new_name = str(new_name or "").strip()
        name_error = tmux_session_name_error(new_name)
        if name_error:
            return {"session": session, "new_name": new_name, "error": name_error}, HTTPStatus.BAD_REQUEST
        if new_name != session and new_name in self.sessions:
            return {"session": session, "new_name": new_name, "error": f"session already exists: {new_name}"}, HTTPStatus.CONFLICT
        if new_name == session:
            return {"session": session, "new_session": new_name, "renamed": False, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

        result = tmux(["rename-session", "-t", tmux_session_target(session), new_name], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux rename-session failed")
            return {"session": session, "new_name": new_name, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.revoke_share_tokens_for_session(session)
        self.refresh_sessions()
        self.log_event(new_name, "session_renamed", f"renamed {session} to {new_name}", {"old_session": session, "new_session": new_name})
        return {"session": session, "new_session": new_name, "renamed": True, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

    @requires_known_session(refresh=True)
    def kill_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        result = tmux(["kill-session", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux kill-session failed")
            return {"session": session, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.revoke_share_tokens_for_session(session)
        self.refresh_sessions()
        self.log_event(None, "session_killed", f"killed {session}", {"session": session})
        return {"session": session, "killed": True, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

    def tmux_scroll(self, session: str, direction: str, lines: int) -> None:
        if session not in self.sessions or direction not in {"up", "down"}:
            return
        bounded_lines = str(max(1, min(lines, 80)))
        target = tmux_session_target(session)
        if direction == "up":
            tmux(["copy-mode", "-e", "-t", target], timeout=1.0)
            command = "scroll-up"
        else:
            command = "scroll-down"
        tmux(["send-keys", "-t", target, "-X", "-N", bounded_lines, command], timeout=1.0)

    @requires_known_session(refresh=True)
    def tmux_copy_selection(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        target = info.selected_pane.target if info and info.selected_pane else tmux_session_target(session)

        def cancel_copy_mode_selection() -> None:
            tmux(["send-keys", "-t", target, "-X", "cancel"], timeout=1.0)

        mode = tmux(["display-message", "-p", "-t", target, "#{pane_in_mode}"], timeout=1.0)
        if mode.returncode != 0:
            error = cmd_error(mode, "tmux pane mode check failed")
            return {"session": session, "target": target, "error": error, "errors": errors}, HTTPStatus.INTERNAL_SERVER_ERROR
        if mode.stdout.strip() != "1":
            return {
                "session": session,
                "target": target,
                "copied": False,
                "text": "",
                "error": "tmux copy mode is not active",
                "errors": errors,
            }, HTTPStatus.OK

        before = tmux(["display-message", "-p", "-t", target, "#{buffer_created}:#{buffer_size}:#{buffer_sample}"], timeout=1.0)
        before_signature = before.stdout.strip() if before.returncode == 0 else ""
        copied = tmux(["send-keys", "-t", target, "-X", "copy-selection-no-clear"], timeout=1.0)
        if copied.returncode != 0:
            error = cmd_error(copied, "tmux copy selection failed")
            return {"session": session, "target": target, "copied": False, "text": "", "error": error, "errors": errors}, HTTPStatus.OK

        after = tmux(["display-message", "-p", "-t", target, "#{buffer_created}:#{buffer_size}:#{buffer_sample}"], timeout=1.0)
        if after.returncode != 0:
            error = cmd_error(after, "tmux buffer check failed")
            return {"session": session, "target": target, "error": error, "errors": errors}, HTTPStatus.INTERNAL_SERVER_ERROR
        if after.stdout.strip() == before_signature:
            cancel_copy_mode_selection()
            return {
                "session": session,
                "target": target,
                "copied": False,
                "text": "",
                "error": "no tmux selection copied",
                "errors": errors,
            }, HTTPStatus.OK

        buffer_result = tmux(["save-buffer", "-"], timeout=1.0)
        if buffer_result.returncode != 0:
            cancel_copy_mode_selection()
            error = cmd_error(buffer_result, "tmux save buffer failed")
            return {"session": session, "target": target, "error": error, "errors": errors}, HTTPStatus.INTERNAL_SERVER_ERROR

        text = buffer_result.stdout
        cancel_copy_mode_selection()
        return {
            "session": session,
            "target": target,
            "copied": bool(text),
            "text": text,
            "chars": len(text),
            "errors": errors,
        }, HTTPStatus.OK

    @requires_known_session(refresh=True)
    def ensure_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        if tmux_has_exact_session(session):
            return {"session": session, "created": False, "ok": True}, HTTPStatus.OK

        self.sessions = [item for item in self.sessions if item != session]
        return {"error": f"session no longer exists: {session}"}, HTTPStatus.NOT_FOUND

    def create_next_session(self, agent: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        agent = agent if agent in AGENT_COMMANDS else "claude"
        available_agents = available_agent_commands()
        if agent not in available_agents:
            return {
                "error": f"{agent} is not available on this server PATH",
                "agent": agent,
                "available_agents": available_agents,
                "sessions": self.sessions,
            }, HTTPStatus.NOT_FOUND
        if len(self.sessions) >= MAX_YOLOMUX_SESSION_TABS:
            return {
                "error": f"maximum session tabs reached: {MAX_YOLOMUX_SESSION_TABS}",
                "sessions": self.sessions,
            }, HTTPStatus.CONFLICT
        session = next_numbered_session_name(self.sessions)
        if session is None:
            return {
                "error": f"no available numbered session names from 1 to {MAX_YOLOMUX_SESSION_TABS}",
                "sessions": self.sessions,
            }, HTTPStatus.CONFLICT
        cwd = session_workdir(session)
        command = agent_command(agent, self.dangerously_yolo)
        result = tmux(
            [
                "new-session",
                "-d",
                "-s",
                session,
                "-c",
                str(cwd),
                command,
            ],
            timeout=5.0,
        )
        if result.returncode != 0:
            error = cmd_error(result, "tmux new-session failed")
            return {"session": session, "created": False, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR
        self.refresh_sessions()
        self.log_event(
            session,
            "session_started",
            f"created {session} with {agent}",
            {"agent": agent, "cwd": str(cwd), "command": command, "dangerously_yolo": self.dangerously_yolo},
        )
        return {
            "session": session,
            "sessions": self.sessions,
            "agent": agent,
            "created": True,
            "cwd": str(cwd),
            "command": command,
            "dangerously_yolo": self.dangerously_yolo,
            "ok": True,
        }, HTTPStatus.OK

    def _save_uploaded_files(self, target_dir: Path, files: list[UploadedFile]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, HTTPStatus]:
        saved: list[dict[str, Any]] = []
        upload_template = settings_payload().get("settings", {}).get("uploads", {}).get("filename_template")
        for upload in files:
            safe_name = sanitize_upload_filename(upload.filename)
            path = unique_upload_path(target_dir, safe_name, str(upload_template or ""))
            try:
                path.write_bytes(upload.content)
            except OSError as exc:
                return [], {
                    "error": f"failed to save {safe_name}: {exc}",
                    "target_dir": str(target_dir),
                }, HTTPStatus.INTERNAL_SERVER_ERROR
            saved.append(
                {
                    "name": upload.filename,
                    "saved_name": path.name,
                    "path": str(path),
                    "size": len(upload.content),
                }
            )
        return saved, None, HTTPStatus.OK

    @requires_known_session()
    def upload_files(self, session: str, files: list[UploadedFile]) -> tuple[dict[str, Any], HTTPStatus]:
        if not files:
            return {"session": session, "error": "no files supplied"}, HTTPStatus.BAD_REQUEST
        if len(files) > UPLOAD_MAX_FILES:
            return {
                "session": session,
                "error": f"too many files; limit is {UPLOAD_MAX_FILES}",
            }, HTTPStatus.REQUEST_ENTITY_TOO_LARGE

        target_dir, target_source = self.upload_target_dir(session)
        if target_dir is None:
            return {
                "session": session,
                "error": f"upload target not found for {session}",
                "target_source": target_source,
            }, HTTPStatus.NOT_FOUND
        if not target_dir.is_dir():
            return {"session": session, "error": f"upload target is not a directory: {target_dir}"}, HTTPStatus.NOT_FOUND

        saved, error, status = self._save_uploaded_files(target_dir, files)
        if error is not None:
            error["session"] = session
            return error, status
        self.log_event(
            session,
            "upload",
            f"uploaded {len(saved)} file{'s' if len(saved) != 1 else ''}",
            {
                "target_dir": str(target_dir),
                "target_source": target_source,
                "files": [item["path"] for item in saved],
                "sizes": [item["size"] for item in saved],
            },
        )
        return {
            "session": session,
            "target_dir": str(target_dir),
            "target_source": target_source,
            "files": saved,
        }, HTTPStatus.OK

    def upload_editor_files(self, files: list[UploadedFile], *, editor_path: str = "", base_dir: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        if not files:
            return {"error": "no files supplied"}, HTTPStatus.BAD_REQUEST
        if len(files) > UPLOAD_MAX_FILES:
            return {"error": f"too many files; limit is {UPLOAD_MAX_FILES}"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        raw_base = str(base_dir or "").strip()
        raw_editor_path = str(editor_path or "").strip()
        if raw_base:
            base = Path(raw_base).expanduser()
            target_source = "editor_base_dir"
        elif raw_editor_path:
            base = Path(raw_editor_path).expanduser().parent
            target_source = "editor_path"
        else:
            return {"error": "missing editor_path or base_dir"}, HTTPStatus.BAD_REQUEST
        if not base.is_dir():
            return {"error": f"editor upload base is not a directory: {base}", "base_dir": str(base)}, HTTPStatus.NOT_FOUND
        target_dir = self._apply_upload_subdir(base)
        if not target_dir.is_dir():
            return {"error": f"upload target is not a directory: {target_dir}", "base_dir": str(base)}, HTTPStatus.NOT_FOUND
        saved, error, status = self._save_uploaded_files(target_dir, files)
        if error is not None:
            error["base_dir"] = str(base)
            return error, status
        for item in saved:
            try:
                item["relative_path"] = Path(item["path"]).relative_to(base).as_posix()
            except ValueError:
                item["relative_path"] = Path(item["path"]).name
        self.log_event(
            "",
            "editor_upload",
            f"uploaded {len(saved)} editor file{'s' if len(saved) != 1 else ''}",
            {
                "target_dir": str(target_dir),
                "target_source": target_source,
                "base_dir": str(base),
                "files": [item["path"] for item in saved],
                "sizes": [item["size"] for item in saved],
            },
        )
        return {
            "target_dir": str(target_dir),
            "target_source": target_source,
            "base_dir": str(base),
            "files": saved,
        }, HTTPStatus.OK

    def run_file_drop_action(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        result, status = run_drop_action(payload)
        return result, HTTPStatus(status)

    def upload_max_bytes(self) -> int:
        value = settings_payload().get("settings", {}).get("uploads", {}).get("max_bytes", UPLOAD_MAX_BYTES)
        return int(value) if isinstance(value, (int, float)) and value > 0 else UPLOAD_MAX_BYTES

    def upload_target_dir(self, session: str) -> tuple[Path | None, str]:
        base, source = self._resolve_upload_base_dir(session)
        if base is None:
            return None, source
        return self._apply_upload_subdir(base), source

    def _apply_upload_subdir(self, base: Path) -> Path:
        # Uploads default into a `.upload/` subdir of the working dir (keeps the cwd/repo clean and
        # easy to .gitignore); the `uploads.subdir` setting overrides it, and an empty value writes
        # straight into the working dir. One owner: every upload routes through upload_target_dir, so
        # the subdir logic lives only here.
        subdir = str(settings_payload().get("settings", {}).get("uploads", {}).get("subdir", DEFAULT_UPLOAD_SUBDIR) or "").strip()
        if not subdir:
            return base
        relative = Path(subdir)
        if relative.is_absolute() or ".." in relative.parts:
            return base  # never let the setting escape the working dir
        target = base / relative
        try:
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o700)
        except OSError:
            return base  # fall back to the working dir if the subdir can't be created
        return target

    def _resolve_upload_base_dir(self, session: str) -> tuple[Path | None, str]:
        focus_root = focus_root_for_session(session)
        if focus_root:
            return Path(focus_root), "session_workdir"
        workdir = session_workdir(session)
        resolved, ok = resolved_upload_dir(workdir)
        if ok:
            return resolved, "session_workdir"

        sessions, _ = discover_sessions([session])
        info = sessions.get(session)
        if info is None:
            return None, "session_workdir"
        git_data = session_git_inventory(info)
        if git_data is not None:
            for key in ("root", "cwd"):
                value = git_data.get(key)
                if isinstance(value, str):
                    resolved, ok = resolved_upload_dir(Path(value))
                    if ok:
                        return resolved, f"git_{key}"
        for cwd in candidate_session_cwds(info):
            resolved, ok = resolved_upload_dir(Path(cwd), allow_home=True)
            if ok:
                return resolved, "pane_current_path"
        return None, "session_workdir"

    @requires_known_session()
    def set_auto_approve(self, session: str, enabled: bool, persist: bool = True, takeover: bool = True) -> tuple[AutoApproveState, HTTPStatus]:
        with self.auto_workers_lock:
            self.prune_auto_approve_workers_locked(session)

            if enabled:
                if self.auto_approve_worker_keys_for_session_locked(session):
                    self.ensure_auto_approve_agent_workers_locked(session, takeover=takeover)
                    return self.auto_approve_session_status(session), HTTPStatus.OK
                if not tmux_has_exact_session(session):
                    return {"session": session, "enabled": False, "error": f"tmux session not found: {session}"}, HTTPStatus.NOT_FOUND
                started, status = self.ensure_auto_approve_agent_workers_locked(session, takeover=takeover)
                if not started:
                    return status, HTTPStatus.CONFLICT
                if persist:
                    self.set_persisted_auto_session(session, True)
                self.log_event(session, "yolo_enabled", "YOLO enabled", {"persist": persist})
                return self.auto_approve_session_status(session), HTTPStatus.OK

            keys = self.auto_approve_worker_keys_for_session_locked(session)
            worker_sessions = self.auto_worker_session_map()
            for key in keys:
                worker = self.auto_workers.pop(key, None)
                worker_sessions.pop(key, None)
                if worker is not None:
                    worker.stop()
            if keys:
                if persist:
                    self.set_persisted_auto_session(session, False)
                self.log_event(session, "yolo_disabled", "YOLO disabled", {"persist": persist})
        return self.auto_approve_session_status(session), HTTPStatus.OK

    def auto_worker_session_map(self) -> dict[str, str]:
        mapping = getattr(self, "auto_worker_sessions", None)
        if not isinstance(mapping, dict):
            mapping = {}
            self.auto_worker_sessions = mapping
        return mapping

    def prune_auto_approve_workers_locked(self, session: str | None = None) -> bool:
        removed = False
        worker_sessions = self.auto_worker_session_map()
        for key, worker in list(self.auto_workers.items()):
            worker_session = worker_sessions.get(key, key)
            if session is not None and worker_session != session:
                continue
            if worker.alive():
                continue
            self.auto_workers.pop(key, None)
            worker_sessions.pop(key, None)
            removed = True
        return removed

    def auto_approve_worker_keys_for_session_locked(self, session: str) -> list[str]:
        worker_sessions = self.auto_worker_session_map()
        return [
            key
            for key in self.auto_workers
            if worker_sessions.get(key, key) == session
        ]

    def auto_approve_agent_targets(self, session: str, payload: dict[str, Any] | None = None) -> list[str]:
        signal_payload = payload if payload is not None else self.tmux_signal_snapshot()
        agents = signal_payload.get("agents") if isinstance(signal_payload, dict) else None
        if not isinstance(agents, list):
            return []
        targets: list[str] = []
        seen: set[str] = set()
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            if str(agent.get("session") or "") != session or agent.get("dead") is True:
                continue
            target = str(agent.get("target") or agent.get("pane_id") or "").strip()
            if not target or target in seen:
                continue
            seen.add(target)
            targets.append(target)
        return targets

    def auto_approve_session_lock_owner(self, session: str) -> dict[str, Any] | None:
        """The owner of session's YO lock when another server holds it, else None.

        YO workers lock per agent-pane target (auto_approve_agent_targets), NOT the bare session,
        so a server without a local worker must probe those pane-target locks to notice another
        server's ownership. The bare session is probed too, covering the no-agent fallback path and
        any legacy session-named lock. Checking only the session lock missed every agent-backed
        session, which is what silently dropped the cross-server "YO running elsewhere" (yellow)
        marker on the other servers.
        """
        targets = self.auto_approve_agent_targets(session) or [session]
        if session not in targets:
            targets = [*targets, session]
        for target in targets:
            owner = auto_approve_lock_owner(target)
            if owner:
                return owner
        return None

    def ensure_auto_approve_agent_workers_locked(self, session: str, takeover: bool) -> tuple[bool, AutoApproveState]:
        worker_sessions = self.auto_worker_session_map()
        desired_targets = self.auto_approve_agent_targets(session) or [session]
        desired = set(desired_targets)
        for key in self.auto_approve_worker_keys_for_session_locked(session):
            if key in desired:
                continue
            worker = self.auto_workers.pop(key, None)
            worker_sessions.pop(key, None)
            if worker is not None:
                worker.stop()
        first_error: AutoApproveState | None = None
        started_any = False
        for target in desired_targets:
            existing = self.auto_workers.get(target)
            if existing is not None and existing.alive():
                started_any = True
                worker_sessions[target] = session
                continue
            worker, status = self.start_auto_approve_worker(session, takeover=takeover, target=target)
            if worker is None:
                if first_error is None:
                    first_error = status
                continue
            self.auto_workers[target] = worker
            worker_sessions[target] = session
            started_any = True
        if started_any:
            return True, {"session": session, "target": session, "enabled": True}
        return False, first_error or {"session": session, "enabled": False, "error": "failed to start YOLO worker"}

    def sync_auto_approve_agent_workers(self, takeover: bool = False) -> None:
        with self.auto_workers_lock:
            self.prune_auto_approve_workers_locked()
            worker_sessions = self.auto_worker_session_map()
            sessions = sorted({
                worker_sessions.get(key, key)
                for key, worker in self.auto_workers.items()
                if worker.alive()
            })
            for session in sessions:
                if session in self.sessions:
                    self.ensure_auto_approve_agent_workers_locked(session, takeover=takeover)

    def start_auto_approve_worker(self, session: str, takeover: bool, target: str | None = None) -> tuple[AutoApproveWorker | None, AutoApproveState]:
        worker_target = str(target or session)
        owner_extra = self.control_server.owner_payload()
        owner_extra["session"] = session
        worker = AutoApproveWorker(
            worker_target,
            interval=self.auto_approve_interval_seconds(),
            event_callback=self.log_auto_event,
            owner_extra=owner_extra,
            dangerously_yolo=self.dangerously_yolo,
            prompt_source=self.auto_approve_prompt_source(),
            capture_gate=self.auto_approve_capture_allowed_for_target,
        )
        started, owner = worker.start()
        if started:
            status = worker.status()
            status["session"] = session
            return worker, status
        locked_owner = owner
        if takeover and self.request_auto_approve_release(session, owner):
            # #69: re-acquire with the SINGLE atomic non-blocking flock (worker.start), retried briefly to
            # absorb any lag between the owner's ok and its flock release. Each attempt is atomic, so a
            # third instance grabbing the lock in the gap simply fails the acquire (reported locked) —
            # never a double-owner.
            deadline = time.monotonic() + 2.0
            while True:
                owner_extra = self.control_server.owner_payload()
                owner_extra["session"] = session
                worker = AutoApproveWorker(
                    worker_target,
                    interval=self.auto_approve_interval_seconds(),
                    event_callback=self.log_auto_event,
                    owner_extra=owner_extra,
                    dangerously_yolo=self.dangerously_yolo,
                    prompt_source=self.auto_approve_prompt_source(),
                    capture_gate=self.auto_approve_capture_allowed_for_target,
                )
                started, owner = worker.start()
                if started:
                    self.log_event(session, "yolo_takeover", "YOLO moved from another server", {"owner": locked_owner or {}})
                    status = worker.status()
                    status["session"] = session
                    return worker, status
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        payload: AutoApproveState = worker.status()
        payload.update({
            "session": session,
            "enabled": False,
            "enabled_elsewhere": True,
            "locked": True,
            "lock_owner": owner,
            "error": auto_approve_lock_message(owner),
        })
        self.log_event(session, "yolo_locked", "YOLO already owned by another server", {"owner": owner or {}})
        return None, payload

    def request_auto_approve_release(self, session: str, owner: dict[str, Any] | None) -> bool:
        request = {
            "action": "disable_auto_approve",
            "session": session,
            "requester": {
                "pid": os.getpid(),
                "hostname": SERVER_HOSTNAME,
                "project_root": str(PROJECT_ROOT),
                "control_socket": str(self.control_server.path),
            },
        }
        response = send_yolomux_control_request(owner, request)
        if response.get("ok") is not True:
            self.log_event(session, "yolo_takeover_failed", "YOLO owner did not release", {"owner": owner or {}, "response": response})
            return False
        # the owner stopped its worker and released the flock before replying ok (it joins the
        # thread first, #70). Do NOT probe-and-poll the lock to "infer" we may take it — that LOCK_EX
        # probe momentarily acquires the lock and races a third instance. Trust the owner's ok; the
        # caller re-acquires with a single atomic non-blocking flock, which is the only safe arbiter.
        return True

    def auto_approve_capture_target(self, session: str, discovered_sessions: dict[str, SessionInfo] | None = None) -> str:
        if discovered_sessions is None:
            infos, _errors = discover_sessions([session])
            info = infos.get(session)
        else:
            info = discovered_sessions.get(session)
        if info is not None:
            selected = info.selected_pane
            agent_targets = {item.pane_target for item in info.agents if item.pane_target}
            if selected is not None and selected.target in agent_targets:
                return selected.target
            agent = next((item for item in info.agents if item.pane_target), None)
            if agent is not None:
                return agent.pane_target
        return session

    def auto_approve_session_has_pending_prompt(self, session: str) -> bool:
        with self.auto_workers_lock:
            worker_sessions = self.auto_worker_session_map()
            workers = [
                worker
                for key, worker in self.auto_workers.items()
                if worker_sessions.get(key, key) == session
            ]
        return any(worker.has_pending_prompt() for worker in workers)

    def prompt_and_screen_status(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        capture_pane: bool = True,
        capture_bare_session_when_roster: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        hidden_prompt = normalized_prompt_state()
        target = self.auto_approve_capture_target(session, discovered_sessions=discovered_sessions)
        capture_roster_target = not capture_pane and discovered_sessions is not None and session in discovered_sessions
        capture_idle_bare_session = capture_bare_session_when_roster and not capture_pane and target == session
        if not capture_roster_target and not capture_idle_bare_session and not self.auto_approve_session_has_pending_prompt(session) and not self.auto_approve_capture_allowed_for_target(target):
            return hidden_prompt, {"key": "idle", "text": "tmux activity quiet"}

        def prompt_classifier(prompt_target: str, visible_text: str, pane_text: str | None, prompt_source: str) -> dict[str, Any]:
            return hybrid_approval_prompt_state(prompt_target, visible_text, pane_text, prompt_source=prompt_source)

        def roster_prompt_classifier(_prompt_target: str, visible_text: str, pane_text: str | None, _prompt_source: str) -> dict[str, Any]:
            if pane_text is None:
                return approval_prompt_state(visible_text)
            return approval_prompt_state(visible_text, pane_text)

        def screen_classifier(visible_text: str, pane_target: str | None) -> dict[str, Any]:
            return dict(agent_screen_state(visible_text, pane_target=pane_target))

        if not capture_pane:
            # Roster path: derive working/idle from the LIVE pane via a cheap visible-only capture
            # plus cheap prompt presence from the already-captured text. This avoids the expensive
            # hybrid transcript / bash double-capture fan-out while still lighting roster approval badges.
            state = classify_agent_pane(
                target,
                session=session,
                discovered_sessions=discovered_sessions,
                prompt_source="pane",
                include_composer=False,
                include_transcript_activity=False,
                capture_full_for_bash=False,
                capture_func=tmux_capture_pane,
                capture_styled_func=tmux_capture_pane_styled,
                prompt_classifier=roster_prompt_classifier,
                screen_classifier=screen_classifier,
            )
            if state.reason_code in {"disconnected", "error"}:
                return hidden_prompt, {"key": "idle", "text": ""}
            return normalized_prompt_state(state.prompt), dict(state.screen)
        state = classify_agent_pane(
            target,
            session=session,
            discovered_sessions=discovered_sessions,
            prompt_source=self.auto_approve_prompt_source(),
            include_composer=False,
            include_transcript_activity=True,
            capture_func=tmux_capture_pane,
            capture_styled_func=tmux_capture_pane_styled,
            prompt_classifier=prompt_classifier,
            screen_classifier=screen_classifier,
            discover_sessions_func=discover_sessions,
        )
        return normalized_prompt_state(state.prompt), dict(state.screen)

    def agent_window_screen_state(
        self,
        agent: AgentInfo,
        preclassified_by_target: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target = str(agent.pane_target or "")
        if target and preclassified_by_target and target in preclassified_by_target:
            return dict(preclassified_by_target[target])
        if not target:
            return {"key": "idle", "text": ""}
        visible_text = tmux_capture_pane(target, visible_only=True)
        if visible_text is None:
            return {"key": "idle", "text": "failed to capture pane"}
        return dict(agent_screen_state(visible_text, pane_target=target))

    @staticmethod
    def agent_window_state_from_screen(screen: dict[str, Any]) -> str:
        key = str(screen.get("key") or "").strip()
        if key == "working":
            return "working"
        if key in {"approval", "needs-approval"}:
            return "approval"
        if key == "needs-input":
            return "needs-input"
        return "idle"

    def activity_record_recency_ts(self, record: dict[str, Any] | None) -> float:
        if not isinstance(record, dict):
            return 0.0
        active_recency = self.float_value(record.get("active_recency_ts"), 0.0)
        if active_recency > 0:
            return active_recency
        return max(
            self.float_value(record.get("last_user_input_ts"), 0.0),
            self.float_value(record.get("last_agent_active_ts"), 0.0),
            self.float_value(record.get("last_output_ts"), 0.0),
        )

    def activity_snapshot_with_recency(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        source = snapshot if isinstance(snapshot, dict) else self.activity_ledger.snapshot()
        result: dict[str, Any] = {}
        for key, value in source.items():
            record = dict(value) if isinstance(value, dict) else {}
            record["active_recency_ts"] = self.activity_record_recency_ts(record)
            result[key] = record
        return result

    def agent_window_last_active_ts(self, activity_snapshot: dict[str, Any], session: str, window: str) -> float:
        key = f"{session}:{window}" if window else session
        record = activity_snapshot.get(key) if isinstance(activity_snapshot, dict) else None
        return self.activity_record_recency_ts(record if isinstance(record, dict) else None)

    def agent_window_status_payloads(
        self,
        session: str,
        *,
        info: SessionInfo | None = None,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        activity_snapshot: dict[str, Any] | None = None,
        preclassified_by_target: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if info is None:
            info = discovered_sessions.get(session) if discovered_sessions is not None else None
        if info is None:
            infos, _errors = discover_sessions([session])
            info = infos.get(session)
        if info is None:
            return []
        activity = self.activity_snapshot_with_recency(activity_snapshot)
        rows: list[dict[str, Any]] = []
        window_names = {str(pane.window or ""): str(pane.window_name or "") for pane in info.panes}
        for agent_index, agent in enumerate(info.agents):
            kind = str(agent.kind or "").lower()
            if kind not in {"claude", "codex"}:
                continue
            window, pane = session_files.agent_window_for_info(info, agent)
            screen = self.agent_window_screen_state(agent, preclassified_by_target=preclassified_by_target)
            state = self.agent_window_state_from_screen(screen)
            elapsed = self.float_value(screen.get("display_elapsed_seconds"), self.float_value(screen.get("status_elapsed_seconds"), -1.0))
            last_active_ts = self.agent_window_last_active_ts(activity, session, window)
            window_index: int | None
            try:
                window_index = int(window)
            except ValueError:
                window_index = None
            window_name = window_names.get(window) or kind
            window_label = f"{window}:{kind}" if window else kind
            rows.append({
                "kind": kind,
                "state": state,
                "window": window,
                "window_index": window_index,
                "window_name": window_name,
                "window_label": window_label,
                "pane": pane,
                "pane_target": str(agent.pane_target or ""),
                "working_elapsed_seconds": elapsed if state == "working" and elapsed >= 0 else None,
                "idle_since": last_active_ts if state == "idle" and last_active_ts > 0 else None,
                "last_active_ts": last_active_ts,
                "screen_text": str(screen.get("text") or ""),
                "status_tokens": screen.get("status_tokens") if isinstance(screen.get("status_tokens"), (int, float)) else None,
                "_agent_order": agent_index,
            })
        state_rank = {"working": 0, "approval": 1, "needs-input": 2, "idle": 3}
        rows.sort(key=lambda item: (state_rank.get(str(item.get("state") or ""), 9), item.get("window_index") if isinstance(item.get("window_index"), int) else 9999, int(item.get("_agent_order") or 0)))
        for item in rows:
            item.pop("_agent_order", None)
        return rows

    def auto_approve_session_status(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        include_live_prompt: bool = True,
        capture_bare_session_when_roster: bool = False,
        activity_snapshot: dict[str, Any] | None = None,
    ) -> AutoApproveState:
        with self.auto_workers_lock:
            worker_sessions = self.auto_worker_session_map()
            worker_items = [
                (key, worker)
                for key, worker in self.auto_workers.items()
                if worker_sessions.get(key, key) == session
            ]
        if worker_items:
            statuses = [worker.status() for _key, worker in worker_items]
            primary = next((status for status in statuses if status.get("target") == session), statuses[0])
            payload: AutoApproveState = dict(primary)
            payload["target"] = session
            payload["worker_target"] = primary.get("target")
            payload["worker_targets"] = [status.get("target") for status in statuses if status.get("target")]
            payload["enabled"] = any(status.get("enabled") is True for status in statuses)
            payload["approved"] = sum(int(status.get("approved") or 0) for status in statuses)
            payload["blocked"] = sum(int(status.get("blocked") or 0) for status in statuses)
            payload["enabled_elsewhere"] = False
            payload["locked"] = False
        else:
            payload = {
                "target": session,
                "enabled": False,
                "enabled_elsewhere": False,
                "locked": False,
                "approved": 0,
                "blocked": 0,
                "last_action": "off",
            }
            owner = self.auto_approve_session_lock_owner(session)
            if owner:
                payload.update({
                    "enabled_elsewhere": True,
                    "locked": True,
                    "lock_owner": owner,
                    "last_action": auto_approve_lock_message(owner),
                    "error": auto_approve_lock_message(owner),
                })
        capture_target = self.auto_approve_capture_target(session, discovered_sessions=discovered_sessions)
        prompt, screen = self.prompt_and_screen_status(
            session,
            discovered_sessions=discovered_sessions,
            capture_pane=include_live_prompt,
            capture_bare_session_when_roster=capture_bare_session_when_roster,
        )
        payload["prompt"] = prompt
        payload["screen"] = screen
        info = discovered_sessions.get(session) if discovered_sessions is not None else None
        payload["agent_windows"] = self.agent_window_status_payloads(
            session,
            info=info,
            discovered_sessions=discovered_sessions,
            activity_snapshot=activity_snapshot,
            preclassified_by_target={capture_target: screen} if capture_target else None,
        )
        return payload

    def auto_approve_status(self, session: str | None = None) -> tuple[AutoApproveState | AutoApproveStatusPayload, HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        if session is not None and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        removed = False
        with self.auto_workers_lock:
            worker_sessions = self.auto_worker_session_map()
            for name, worker in list(self.auto_workers.items()):
                if not worker.alive():
                    worker_session = worker_sessions.get(name, name)
                    self.log_event(worker_session, "worker_stopped", "YOLO worker stopped", worker.status())
                    self.auto_workers.pop(name, None)
                    worker_sessions.pop(name, None)
                    removed = True
            self.sync_auto_approve_agent_workers(takeover=False)
        if removed:
            self.persist_auto_sessions()
        activity_snapshot = self.activity_snapshot_with_recency()
        if session is not None:
            return self.auto_approve_session_status(session, activity_snapshot=activity_snapshot), HTTPStatus.OK
        discovered_sessions, discovery_errors = discover_sessions(self.sessions)
        return {
            "session_order": self.sessions,
            "sessions": {
                name: self.auto_approve_session_status(
                    name,
                    discovered_sessions=discovered_sessions,
                    include_live_prompt=False,
                    capture_bare_session_when_roster=True,
                    activity_snapshot=activity_snapshot,
                )
                for name in self.sessions
            },
            "errors": [*refresh_errors, *discovery_errors],
            "rules": self.yolo_rules_payload(),
        }, HTTPStatus.OK

    def stop_auto_approve_all(self) -> None:
        with self.auto_workers_lock:
            for worker in list(self.auto_workers.values()):
                worker.stop()
            self.auto_workers.clear()
            self.auto_worker_session_map().clear()
        self.close_yoagent_codex_app_server()
        self.control_server.stop()

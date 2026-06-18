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
from .common import SERVER_HOSTNAME
from .common import SERVER_STARTED_AT
from .common import SUMMARY_CODEX_SERVICE_TIER
from .common import SUMMARY_MAX_PROMPT_CHARS
from .common import WATCH_INDEX_PATH
from .common import YOLOMUX_VERSION
from .common import YOAGENT_CLAUDE_SUMMARY_MODEL
from .common import UPLOAD_MAX_FILES
from .common import UPLOAD_MAX_BYTES
from .common import as_dict
from .common import codex_exec_argv
from .common import codex_runtime_env
from .common import next_numbered_session_name
from .common import tail_file_lines
from .common import truncate_text
from .control import YolomuxControlServer
from .control import send_yolomux_control_request
from .drop_actions import run_drop_action
from .events import EventLog
from .events import read_yolomux_state
from .events import update_yolomux_state
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
from .settings import save_settings
from .settings import SETTINGS_PATH
from .settings import settings_payload
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
from .transcripts import session_transcript_activity_state
from .transcripts import trim_prompt_text
from .prompt_detector import agent_screen_state
from .prompt_detector import approval_prompt_state
from .tmux_utils import cmd_error
from .tmux_utils import list_tmux_session_names
from .tmux_utils import tmux
from .tmux_utils import tmux_clear_input
from .tmux_utils import tmux_capture_pane
from .tmux_utils import tmux_has_exact_session
from .tmux_utils import tmux_paste_text
from .tmux_utils import tmux_session_target
from .tmux_signals import fetch_tmux_signal_snapshot
from .tmux_signals import TmuxSignalEventWatcher
from .types import AutoApproveState
from .types import AutoApproveStatusPayload
from .types import RunHistoryEntry
from .types import RunHistoryPayload
from .uploads import sanitize_upload_filename
from .uploads import unique_upload_path
from .web import server_string
from .workdir import agent_command
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_status
from .workdir import available_agent_commands
from .workdir import resolved_upload_dir
from .workdir import session_workdir
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


logger = logging.getLogger(__name__)
METADATA_BADGE_PULSE_SECONDS = 20.0
YOAGENT_ACTION_PREVIEW_TTL_SECONDS = 5 * 60
YOAGENT_ACTION_TEXT_LIMIT = 4000
YOAGENT_ACTION_RESULT_WAIT_SECONDS = 180.0
YOAGENT_ACTION_RESULT_POLL_SECONDS = 1.0
YOAGENT_ACTION_RESULT_MAX_CHARS = 6000
YOAGENT_ACTION_AGENT_KINDS = {"claude", "codex"}
YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS = {"idle", "done", "needs-input", "input-draft"}
YOAGENT_JOBS_STATE_KEY = "yoagent_jobs"
YOAGENT_JOB_MAX_ITEMS = 200
YOAGENT_JOB_POLL_SECONDS = 1.0
YOAGENT_JOB_DEFAULT_TIMEOUT_MINUTES = 120
YOAGENT_JOB_IDLE_QUIET_SECONDS = 3.0
METADATA_BADGES = ("main", "pr", "status", "ci")
METADATA_BADGE_SIGNATURES_STATE_KEY = "metadata_badge_signatures"
METADATA_BADGE_PULSE_UNTIL_STATE_KEY = "metadata_badge_pulse_until"
YOAGENT_CLI_TIMEOUT_SECONDS = 45
YOAGENT_SESSION_SUMMARIES_STATE_KEY = "yoagent_session_summaries"
YOAGENT_SESSION_SUMMARY_STATES = {"working", "waiting", "blocked", "done", "idle"}
YOAGENT_SESSION_SUMMARY_QUIET_SECONDS = 15.0
YOAGENT_SESSION_SUMMARY_MAX_ITEMS = 120
YOAGENT_STARTUP_QUESTION = (
    "The user just opened YO!agent. Read the supplied activity context and give a concise first "
    "assistant response: what looks active, what may need attention, and one concrete next step. "
    "Keep it short and answer as YO!agent."
)
YOAGENT_AUTH_FAILURE_RE = re.compile(
    r"(not\s+logged\s+in|log\s*in|login|required\s+auth|authentication|unauthorized|permission\s+denied|401)",
    re.IGNORECASE,
)
YOAGENT_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
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
DEFAULT_TABBER_ACTIVITY_REFRESH_SECONDS = 15.0
SERVER_ACTIVITY_HEARTBEAT_ROTATE_SECONDS = 3600.0
CLIENT_WATCH_ROOT_TTL_SECONDS = 300
CLIENT_WATCH_ROOT_LIMIT = 128
CLIENT_WATCH_FILE_LIMIT = 128
DIRECTORY_WATCH_ENTRY_LIMIT = 512
# Keep in sync with tmuxSessionNameError() in static/yolomux.js.
TMUX_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,64}$")


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


def yoagent_cli_auth_failure(text: str) -> bool:
    return bool(YOAGENT_AUTH_FAILURE_RE.search(text or ""))


def strip_yoagent_hidden_thinking(text: str) -> tuple[str, bool]:
    value = str(text or "")
    cleaned, count = YOAGENT_THINK_BLOCK_RE.subn("", value)
    return cleaned.strip(), count > 0


def strip_yoagent_stream_hidden_thinking(text: str) -> tuple[str, bool]:
    value = str(text or "")
    cleaned, count = YOAGENT_THINK_BLOCK_RE.subn("", value)
    open_think = re.search(r"<think\b[^>]*>", cleaned, re.IGNORECASE)
    if open_think:
        return cleaned[: open_think.start()].strip(), True
    cleaned, close_count = re.subn(r"</think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(), bool(count or close_count)


def yoagent_response_details(response: dict[str, Any]) -> str:
    timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
    cli = response.get("cli") if isinstance(response.get("cli"), dict) else {}
    lines: list[str] = []
    backend_used = str(response.get("backend_used") or response.get("backend") or "").strip()
    if backend_used:
        lines.append(f"- backend: `{backend_used}`")
    ttfr_ms = timing.get("ttfr_ms")
    if isinstance(ttfr_ms, (int, float)):
        lines.append(f"- response time: `{float(ttfr_ms) / 1000:.3f}s` (`{float(ttfr_ms):.1f}ms`)")
    elapsed_ms = cli.get("elapsed_ms")
    if isinstance(elapsed_ms, (int, float)):
        lines.append(f"- model CLI time: `{float(elapsed_ms) / 1000:.3f}s`")
    prompt_chars = cli.get("prompt_chars")
    if isinstance(prompt_chars, int):
        lines.append(f"- prompt size: `{prompt_chars}` chars")
    if "resumed" in cli:
        lines.append(f"- model session: `{'resumed' if cli.get('resumed') else 'seeded'}`")
    if cli.get("context_changed"):
        lines.append("- activity context changed before this model call")
    fallback_reason = str(response.get("fallback_reason") or "").strip()
    if fallback_reason:
        lines.append(f"- fallback reason: {fallback_reason}")
    if response.get("hidden_thinking_removed"):
        lines.append("- raw model thinking was hidden; YOLOmux shows safe diagnostics instead of chain-of-thought")
    return "\n".join(lines)


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


def yoagent_cli_fallback_reason(backend: str, error: str) -> str:
    text = truncate_text(" ".join(str(error or "").split()), 600)
    if not text:
        return ""
    if not yoagent_cli_auth_failure(text):
        return text
    label = "Claude CLI" if backend == "claude" else "Codex CLI" if backend == "codex" else f"{backend} CLI"
    # Use the canonical login command (verified `claude auth login`, not `claude login`).
    login_command = AGENT_LOGIN_COMMANDS.get(backend, f"{backend} login")
    return f"{label} is not logged in. Run `{login_command}`; showing the No agent YO!agent summary."


def yoagent_language_directive(locale: str) -> str:
    locale_id = str(locale or "").strip()
    if locale_id in {"", "en", "en-XA", "system"}:
        return ""
    directive = server_string(locale_id, "yoagent.prompt.answerLanguage").strip()
    return f"\n\n{directive}" if directive else ""


def resolve_yoagent_backend(backend: str) -> str:
    # the default backend is "auto" — prefer codex, then claude, falling back to the
    # deterministic ("No agent") summary if neither is installed AND logged in. Explicit choices
    # (claude / codex / deterministic) pass through unchanged.
    if backend != "auto":
        return backend
    status = agent_auth_status()
    for agent in ("codex", "claude"):
        entry = status.get(agent, {})
        if entry.get("installed") and entry.get("logged_in"):
            return agent
    return "deterministic"


def codex_event_session_id(event: dict[str, Any]) -> str:
    for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id", "conversationId"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("session", "thread", "conversation"):
        value = event.get(key)
        if isinstance(value, dict):
            nested_id = value.get("id")
            if isinstance(nested_id, str) and nested_id:
                return nested_id
            nested = codex_event_session_id(value)
            if nested:
                return nested
    return ""


def yoagent_activity_payload_signature(activity_payload: dict[str, Any]) -> str:
    try:
        return json.dumps(activity_payload, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(activity_payload)


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
        self.yoagent_managed_targets: dict[str, dict[str, Any]] = {}
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
        self.load_metadata_badge_state()
        self.load_yoagent_session_summaries()
        self.event_log = EventLog(EVENT_LOG_PATH)
        self.control_server = YolomuxControlServer(self.handle_control_request)
        self.control_server.start()
        self.maybe_start_yoagent_summary_worker()

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

    def server_event_poll_seconds(self) -> float:
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get("server_event_poll_ms", 850) if isinstance(performance, dict) else 850
        return max(0.25, min(60.0, self.float_value(value, 850.0) / 1000.0))

    def server_directory_event_poll_seconds(self) -> float:
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get("server_directory_event_poll_ms", 3000) if isinstance(performance, dict) else 3000
        return max(0.25, min(60.0, self.float_value(value, 3000.0) / 1000.0))

    def server_background_file_event_poll_seconds(self) -> float:
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get("server_background_file_event_poll_ms", 5000) if isinstance(performance, dict) else 5000
        return max(0.25, min(60.0, self.float_value(value, 5000.0) / 1000.0))

    def server_auto_approve_event_poll_seconds(self) -> float:
        return SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS

    def server_tmux_signal_event_poll_seconds(self) -> float:
        return SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS

    def server_watched_pr_event_poll_seconds(self) -> float:
        return SERVER_WATCHED_PR_EVENT_POLL_SECONDS

    def tmux_signal_snapshot(self, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = self.tmux_signal_cache.get_or_miss("snapshot")
            if cached is not CACHE_MISS:
                return copy.deepcopy(cached)
        payload = fetch_tmux_signal_snapshot()
        self.tmux_signal_cache.set("snapshot", copy.deepcopy(payload))
        return payload

    def tmux_signals_payload(self, force: bool = False) -> tuple[dict[str, Any], HTTPStatus]:
        payload = self.tmux_signal_snapshot(force=force)
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

    def _spawn_self_restart(self) -> bool:
        # Restart the checkout that is running this process. The update path pulls and builds in the
        # same PROJECT_ROOT, so dev worktrees can safely bounce themselves without touching prod.
        try:
            restart_argv = [sys.executable or "python3", *sys.argv]
            restart_cmd = (
                "sleep 1; "
                f"kill {os.getpid()} 2>/dev/null || true; "
                "sleep 2; "
                f"kill -9 {os.getpid()} 2>/dev/null || true; "
                f"cd {shlex.quote(str(common.PROJECT_ROOT))} && "
                "nohup env PYTHONUNBUFFERED=1 "
                f"{' '.join(shlex.quote(arg) for arg in restart_argv)} "
                ">> /tmp/yolomux-self-update-restart.log 2>&1 < /dev/null &"
            )
            subprocess.Popen([
                "nohup", "bash", "-lc", restart_cmd,
            ],
                             cwd=str(common.PROJECT_ROOT), stdin=subprocess.DEVNULL,
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
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get("tabber_activity_refresh_ms", DEFAULT_TABBER_ACTIVITY_REFRESH_SECONDS * 1000) if isinstance(performance, dict) else DEFAULT_TABBER_ACTIVITY_REFRESH_SECONDS * 1000
        return max(1.0, min(60.0, self.float_value(value, DEFAULT_TABBER_ACTIVITY_REFRESH_SECONDS * 1000) / 1000.0))

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
            hours = max(1.0, min(168.0, self.float_value(item.get("hours"), 24.0)))
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
        return {"locale": locale, "visible": visible}

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
        _context_items, _session_files, activity_summary = self.client_watch_state_snapshot()
        if activity_summary.get("visible") is not True:
            return []
        started = time.perf_counter()
        payload = self.activity_summary_payload(locale=str(activity_summary.get("locale") or "en"))
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

    def activity_summary_payload(self, force: bool = False, locale: str = "en") -> dict[str, Any]:
        locale = str(locale or "en").strip() or "en"
        sessions, errors = discover_sessions(self.sessions)
        ordered_sessions = self.tmux_recency_ordered_sessions(self.sessions)
        self.warm_metadata_cache_async(sessions)
        self.prune_yoagent_session_summaries(set(sessions))
        summaries: dict[str, Any] = {}
        ordered_summaries: list[dict[str, Any]] = []
        session_files_by_session: dict[str, dict[str, Any]] = {}
        with self.activity_summary_lock:
            if force:
                self.activity_summary_cache.clear()
                self.clear_session_files_cache()
            for session in ordered_sessions:
                info = sessions.get(session)
                if info is None:
                    continue
                project = session_project_metadata(info, self.metadata_cache, allow_network=False)
                files_payload = self.cached_session_files_payload_for_info(info, hours=24.0)
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
            for session in list(self.activity_summary_cache):
                if session not in sessions:
                    self.activity_summary_cache.pop(session, None)
        generated = datetime.now(timezone.utc)
        rolling_updated = self.latest_yoagent_session_summary_updated_ts()
        settings = self.yoagent_settings()
        return {
            "generated_at": generated.isoformat(),
            "generated_ts": generated.timestamp(),
            "session_order": [session for session in ordered_sessions if session in summaries],
            "sessions": summaries,
            "agents": self.tabber_activity_agents_snapshot(force=force),
            "global": build_global_activity_summary(ordered_summaries, errors),
            "capabilities": yoagent_capabilities_payload(),
            "errors": errors,
            "locale": locale,
            "yoagent_summaries": {
                "auto_refresh": self.yoagent_refresh_interval_seconds(settings) > 0,
                "refresh_interval_seconds": self.yoagent_refresh_interval_seconds(settings),
                "updated_ts": rolling_updated,
                "updated_at": datetime.fromtimestamp(rolling_updated, timezone.utc).isoformat() if rolling_updated else "",
            },
        }

    def sanitized_yoagent_session_summaries(self, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        clean: dict[str, dict[str, Any]] = {}
        for session, item in value.items():
            if not isinstance(session, str) or not isinstance(item, dict):
                continue
            summary = truncate_text(str(item.get("rolling_summary") or "").strip(), 1200)
            if not summary:
                continue
            state = str(item.get("state") or "idle").strip().lower()
            if state not in YOAGENT_SESSION_SUMMARY_STATES:
                state = "idle"
            clean[session] = {
                "rolling_summary": summary,
                "last_processed_ts": max(0.0, self.float_value(item.get("last_processed_ts"), 0.0)),
                "updated_ts": max(0.0, self.float_value(item.get("updated_ts"), 0.0)),
                "state": state,
            }
        return clean

    def load_yoagent_session_summaries(self) -> None:
        state = read_yolomux_state()
        with self.yoagent_session_summary_lock:
            self.yoagent_session_summaries = self.sanitized_yoagent_session_summaries(
                state.get(YOAGENT_SESSION_SUMMARIES_STATE_KEY)
            )

    def persist_yoagent_session_summaries_locked(self) -> None:
        update_yolomux_state({YOAGENT_SESSION_SUMMARIES_STATE_KEY: self.yoagent_session_summaries})

    def prune_yoagent_session_summaries(self, live_sessions: set[str]) -> None:
        with self.yoagent_session_summary_lock:
            stale = [session for session in self.yoagent_session_summaries if session not in live_sessions]
            if not stale:
                return
            for session in stale:
                self.yoagent_session_summaries.pop(session, None)
            self.persist_yoagent_session_summaries_locked()

    def attach_yoagent_session_summary(self, session: str, summary: dict[str, Any]) -> None:
        with self.yoagent_session_summary_lock:
            rolling = dict(self.yoagent_session_summaries.get(session) or {})
        if not rolling.get("rolling_summary"):
            return
        summary["rolling_summary"] = rolling["rolling_summary"]
        summary["rolling_state"] = rolling.get("state") or "idle"
        summary["rolling_updated_ts"] = rolling.get("updated_ts") or 0

    def latest_yoagent_session_summary_updated_ts(self) -> float:
        with self.yoagent_session_summary_lock:
            return max((float(item.get("updated_ts") or 0) for item in self.yoagent_session_summaries.values()), default=0.0)

    def float_value(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def yoagent_refresh_interval_seconds(self, settings: dict[str, Any] | None = None) -> float:
        value = (settings or self.yoagent_settings()).get("refresh_interval_seconds", 0)
        interval = self.float_value(value, 0.0)
        if interval <= 0:
            return 0.0
        return max(30.0, min(3600.0, interval))

    def yoagent_session_summary_due(self, session: str, interval: float, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self.yoagent_session_summary_lock:
            updated = self.float_value((self.yoagent_session_summaries.get(session) or {}).get("updated_ts"), 0.0)
        return updated <= 0 or current - updated >= interval

    def build_yoagent_session_summary_update_prompt(
        self,
        session: str,
        transcript_path: str,
        prior_summary: str,
        transcript_text: str,
    ) -> str:
        return "\n\n".join([
            "Update one YO!agent rolling summary for a single tmux session.",
            f"tmux session: `{session}`",
            f"transcript: `{transcript_path}`",
            "Use the prior summary plus only the new transcript lines below. Do not invent facts.",
            "Return exactly two fields:\nstate: working|waiting|blocked|done|idle\nsummary: 1-3 short factual lines about what this session is doing now.",
            "Prior summary:\n" + (prior_summary or "(none)"),
            "New transcript lines:\n" + transcript_text,
        ])

    def parse_yoagent_session_summary_response(self, text: str, default_state: str = "idle") -> dict[str, str]:
        raw = str(text or "").strip()
        state = default_state if default_state in YOAGENT_SESSION_SUMMARY_STATES else "idle"
        match = re.search(r"(?im)^\s*state\s*:\s*([a-z-]+)\s*$", raw)
        if match and match.group(1).lower() in YOAGENT_SESSION_SUMMARY_STATES:
            state = match.group(1).lower()
        summary_match = re.search(r"(?ims)^\s*summary\s*:\s*(.+)$", raw)
        summary = summary_match.group(1).strip() if summary_match else raw
        summary = re.sub(r"(?im)^\s*state\s*:\s*[a-z-]+\s*$", "", summary).strip()
        return {"state": state, "rolling_summary": truncate_text(summary, 1200)}

    def yoagent_codex_app_server_target(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        current_settings = settings or self.yoagent_settings()
        model = str(current_settings.get("codex_model") or "").strip()
        effort = str(current_settings.get("codex_effort") or "").strip()
        target: dict[str, Any] = {
            "session": "__yoagent_codex__",
            "agent_kind": "codex",
            "transport": "codex-app-server",
            "managed": True,
            "cwd": str(PROJECT_ROOT),
            "sandbox": "read-only",
            "approval_policy": "never",
            "approvals_reviewer": "user",
            "ephemeral": False,
            "service_tier": SUMMARY_CODEX_SERVICE_TIER,
        }
        if model:
            target["agent_model"] = model
        if effort:
            target["agent_effort"] = effort
        return target

    def yoagent_codex_app_server_target_key(self, target: dict[str, Any]) -> str:
        return json.dumps(
            {
                "cwd": str(target.get("cwd") or ""),
                "model": str(target.get("agent_model") or target.get("model") or ""),
                "effort": str(target.get("agent_effort") or target.get("effort") or ""),
                "service_tier": str(target.get("service_tier") or ""),
                "sandbox": str(target.get("sandbox") or target.get("sandbox_mode") or ""),
                "approval_policy": str(target.get("approval_policy") or target.get("approvalPolicy") or ""),
            },
            sort_keys=True,
        )

    def close_yoagent_codex_app_server(self) -> None:
        with self.yoagent_codex_app_server_lock:
            if self.yoagent_codex_app_server is not None:
                self.yoagent_codex_app_server.close()
            self.yoagent_codex_app_server = None
            self.yoagent_codex_app_server_key = ""

    def ensure_yoagent_codex_app_server(self, settings: dict[str, Any] | None = None, session_id: str = "") -> tuple[str, str, dict[str, Any]]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", {"transport": "codex-app-server", "persistent": True}
        target = self.yoagent_codex_app_server_target(settings)
        if session_id:
            target["agent_session_id"] = session_id
        key = self.yoagent_codex_app_server_target_key(target)
        started = time.monotonic()
        with self.yoagent_codex_app_server_lock:
            if self.yoagent_codex_app_server is None or self.yoagent_codex_app_server_key != key:
                if self.yoagent_codex_app_server is not None:
                    self.yoagent_codex_app_server.close()
                self.yoagent_codex_app_server = CodexAppServerSession(target)
                self.yoagent_codex_app_server_key = key
            try:
                thread_id, status = self.yoagent_codex_app_server.ensure_started(target, timeout=YOAGENT_CLI_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                self.close_yoagent_codex_app_server()
                return "", str(exc), {"transport": "codex-app-server", "persistent": True}
        status["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return thread_id, "", status

    def run_yoagent_codex_app_server(
        self,
        prompt: str,
        session_id: str = "",
        resume: bool = False,
        settings: dict[str, Any] | None = None,
        stream_callback: Any | None = None,
    ) -> tuple[str, str, str, dict[str, Any]]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", "", {"transport": "codex-app-server", "persistent": True}
        target = self.yoagent_codex_app_server_target(settings)
        if resume and session_id:
            target["agent_session_id"] = session_id
        key = self.yoagent_codex_app_server_target_key(target)
        started = time.monotonic()
        with self.yoagent_codex_app_server_lock:
            if self.yoagent_codex_app_server is None or self.yoagent_codex_app_server_key != key:
                if self.yoagent_codex_app_server is not None:
                    self.yoagent_codex_app_server.close()
                self.yoagent_codex_app_server = CodexAppServerSession(target)
                self.yoagent_codex_app_server_key = key
            result, status = self.yoagent_codex_app_server.send(prompt, target, timeout=YOAGENT_CLI_TIMEOUT_SECONDS, on_event=stream_callback)
            captured_session_id = self.yoagent_codex_app_server.thread_id
        status["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        if result.ok and result.text:
            return result.text, "", captured_session_id, status
        return "", result.error or "codex app-server completed without a final agent message", captured_session_id, status

    def run_yoagent_direct_prompt_backend(self, backend: str, prompt: str, settings: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}
        started = time.monotonic()
        current_settings = settings or self.yoagent_settings()
        if backend == "codex":
            answer, error, _session_id = self.run_yoagent_codex_cli(prompt, session_id="", resume=False, settings=current_settings)
        else:
            claude_model = str(current_settings.get("claude_model") or YOAGENT_CLAUDE_SUMMARY_MODEL).strip()
            claude_effort = str(current_settings.get("claude_effort") or "").strip()
            answer, error = self.run_yoagent_claude_cli(prompt, session_id="", resume=False, model=claude_model, effort=claude_effort)
        return answer, yoagent_cli_fallback_reason(backend, error), {
            "backend": backend,
            "prompt_chars": len(prompt),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "direct": True,
        }

    def update_yoagent_session_summary(
        self,
        session: str,
        info: SessionInfo,
        settings: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        agent = next((item for item in info.agents if item.transcript), None)
        if agent is None or not agent.transcript:
            return {"session": session, "updated": False, "reason": "no transcript"}
        transcript_path = Path(agent.transcript)
        try:
            text = tail_file_lines(transcript_path, MAX_TRANSCRIPT_TAIL_LINES)
        except OSError as exc:
            return {"session": session, "updated": False, "reason": str(exc)}
        if not force and transcript_activity_is_recent(transcript_path, text, recency_seconds=YOAGENT_SESSION_SUMMARY_QUIET_SECONDS):
            return {"session": session, "updated": False, "reason": "transcript still active"}
        with self.yoagent_session_summary_lock:
            previous = dict(self.yoagent_session_summaries.get(session) or {})
        last_processed_ts = self.float_value(previous.get("last_processed_ts"), 0.0)
        since = datetime.fromtimestamp(last_processed_ts + 0.000001, timezone.utc) if last_processed_ts > 0 else datetime.fromtimestamp(0, timezone.utc)
        items, stats = compact_transcript_items_since(text, since)
        items = items[-YOAGENT_SESSION_SUMMARY_MAX_ITEMS:]
        newest = newest_transcript_timestamp(text)
        if not items or newest is None:
            return {"session": session, "updated": False, "reason": "no new transcript lines", "stats": stats}
        transcript_text = "\n\n".join(format_transcript_item(item) for item in items)
        transcript_text, truncated = trim_prompt_text(transcript_text, SUMMARY_MAX_PROMPT_CHARS)
        prompt = self.build_yoagent_session_summary_update_prompt(
            session,
            str(transcript_path),
            str(previous.get("rolling_summary") or ""),
            transcript_text,
        )
        current_settings = settings or self.yoagent_settings()
        requested_backend = str(current_settings.get("backend") or "deterministic").strip().lower()
        backend = resolve_yoagent_backend(requested_backend)
        invocation = str(current_settings.get("invocation") or "cli").strip().lower()
        if backend not in {"codex", "claude"} or invocation != "cli":
            return {"session": session, "updated": False, "reason": "no CLI backend available", "backend": backend}
        answer, fallback_reason, cli_status = self.run_yoagent_direct_prompt_backend(backend, prompt, settings=current_settings)
        if not answer:
            return {"session": session, "updated": False, "reason": fallback_reason or "empty summary response", "backend": backend, "cli": cli_status}
        parsed = self.parse_yoagent_session_summary_response(answer, default_state="working" if agent.status == "running" else "idle")
        if not parsed["rolling_summary"]:
            return {"session": session, "updated": False, "reason": "empty parsed summary", "backend": backend, "cli": cli_status}
        now = time.time()
        record = {
            "rolling_summary": parsed["rolling_summary"],
            "last_processed_ts": newest.timestamp(),
            "updated_ts": now,
            "state": parsed["state"],
        }
        with self.yoagent_session_summary_lock:
            self.yoagent_session_summaries[session] = record
            self.persist_yoagent_session_summaries_locked()
        return {
            "session": session,
            "updated": True,
            "backend": backend,
            "state": parsed["state"],
            "items": len(items),
            "truncated": truncated,
            "stats": stats,
            "cli": cli_status,
        }

    def tick_yoagent_session_summaries(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        current_settings = settings or self.yoagent_settings()
        interval = self.yoagent_refresh_interval_seconds(current_settings)
        if interval <= 0:
            return {"enabled": False, "updated": [], "skipped": []}
        sessions, errors = discover_sessions(self.sessions)
        self.prune_yoagent_session_summaries(set(sessions))
        updated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        now = time.time()
        for session in self.tmux_recency_ordered_sessions(self.sessions):
            info = sessions.get(session)
            if info is None:
                continue
            if not self.yoagent_session_summary_due(session, interval, now):
                skipped.append({"session": session, "reason": "interval"})
                continue
            result = self.update_yoagent_session_summary(session, info, current_settings)
            if result.get("updated"):
                updated.append(result)
            else:
                skipped.append(result)
        return {"enabled": True, "updated": updated, "skipped": skipped, "errors": errors}

    def maybe_start_yoagent_summary_worker(self) -> None:
        settings = self.yoagent_settings()
        if self.yoagent_refresh_interval_seconds(settings) <= 0:
            return
        with self.yoagent_summary_worker_lock:
            if self.yoagent_summary_worker_running:
                return
            self.yoagent_summary_worker_running = True
        threading.Thread(target=self.yoagent_summary_worker_loop, name="yoagent-summary-refresh", daemon=True).start()

    def yoagent_summary_worker_loop(self) -> None:
        try:
            while True:
                settings = self.yoagent_settings()
                interval = self.yoagent_refresh_interval_seconds(settings)
                if interval <= 0:
                    return
                self.tick_yoagent_session_summaries(settings)
                time.sleep(min(interval, 60.0))
        finally:
            with self.yoagent_summary_worker_lock:
                self.yoagent_summary_worker_running = False

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
        created_at: str = "",
    ) -> None:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return
        payload: dict[str, Any] = {
            "stream_id": safe_stream_id,
            "content": truncate_text(str(content or ""), 20_000),
            "done": bool(done),
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
        if backend:
            payload["backend"] = backend
        if phase:
            payload["phase"] = phase
        if hidden_thinking_removed:
            payload["hidden_thinking_removed"] = True
        self.publish_client_event("yoagent_stream_delta", payload, trigger="yoagent_stream", cache="delta")

    def yoagent_stream_callback(self, stream_id: str, backend: str) -> Any:
        state: dict[str, Any] = {"last_content": None, "hidden_thinking_removed": False}

        def callback(event: dict[str, Any]) -> None:
            event_type = str(event.get("event") or "")
            if event_type == "thinking":
                state["hidden_thinking_removed"] = True
                self.publish_yoagent_stream_delta(
                    stream_id,
                    str(state.get("last_content") or ""),
                    backend=backend,
                    phase="thinking",
                    hidden_thinking_removed=True,
                )
                return
            visible_text, hidden_thinking_removed = strip_yoagent_stream_hidden_thinking(str(event.get("text") or ""))
            hidden_changed = bool(hidden_thinking_removed and not state.get("hidden_thinking_removed"))
            state["hidden_thinking_removed"] = bool(state.get("hidden_thinking_removed") or hidden_thinking_removed)
            if visible_text == state.get("last_content") and not hidden_changed:
                return
            state["last_content"] = visible_text
            self.publish_yoagent_stream_delta(
                stream_id,
                visible_text,
                backend=backend,
                phase="answer" if visible_text else "thinking",
                hidden_thinking_removed=bool(state.get("hidden_thinking_removed")),
            )

        return callback

    def yoagent_job_prompt_text(self, action: dict[str, Any]) -> str:
        if str(action.get("type") or "") != "send_prompt":
            return ""
        return truncate_text(str(action.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)

    def yoagent_job_transport_id(self, target: dict[str, Any], action: dict[str, Any], result: dict[str, Any] | None = None) -> str:
        send_result = result.get("send") if isinstance(result, dict) and isinstance(result.get("send"), dict) else {}
        action_result = result.get("action") if isinstance(result, dict) and isinstance(result.get("action"), dict) else {}
        action_target = action_result.get("target") if isinstance(action_result.get("target"), dict) else {}
        candidates = [
            send_result.get("transport"),
            action.get("transport"),
            target.get("transport"),
            action_target.get("transport"),
        ]
        for candidate in candidates:
            raw = str(candidate or "").strip()
            if raw:
                return normalize_yoagent_transport_id(raw)
        return ""

    def yoagent_job_result_marker_from_result(self, result: dict[str, Any]) -> dict[str, Any]:
        send_result = result.get("send") if isinstance(result.get("send"), dict) else {}
        marker = result.get("result_marker") if isinstance(result.get("result_marker"), dict) else send_result.get("result_marker")
        return dict(marker) if isinstance(marker, dict) else {}

    def yoagent_job_result_source_from_result(self, result: dict[str, Any]) -> str:
        send_result = result.get("send") if isinstance(result.get("send"), dict) else {}
        return str(result.get("result_source") or send_result.get("result_source") or result.get("source") or "")

    def sanitize_yoagent_job(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        job_id = str(value.get("id") or "").strip()
        if not job_id:
            return None
        status = str(value.get("status") or "queued")
        if status not in {"queued", "pending_confirmation", "fired", "cancelled", "failed", "timed_out"}:
            status = "queued"
        target = value.get("target") if isinstance(value.get("target"), dict) else {}
        predicate = value.get("predicate") if isinstance(value.get("predicate"), dict) else {}
        action = value.get("action") if isinstance(value.get("action"), dict) else {}
        result = value.get("result") if isinstance(value.get("result"), dict) else {}
        prompt = truncate_text(str(value.get("prompt") or self.yoagent_job_prompt_text(action)), YOAGENT_ACTION_TEXT_LIMIT)
        result_marker = value.get("result_marker") if isinstance(value.get("result_marker"), dict) else self.yoagent_job_result_marker_from_result(result)
        job = {
            "id": job_id,
            "job_id": str(value.get("job_id") or job_id),
            "type": str(value.get("type") or "notify_session_idle"),
            "target": dict(target),
            "predicate": dict(predicate),
            "action": dict(action),
            "transport": str(value.get("transport") or self.yoagent_job_transport_id(target, action, result)),
            "prompt": prompt,
            "prompt_preview": redacted_action_preview(prompt),
            "public_text": redacted_action_text(prompt, 240),
            "started_at": str(value.get("started_at") or ""),
            "result_marker": dict(result_marker),
            "result_source": str(value.get("result_source") or self.yoagent_job_result_source_from_result(result)),
            "created_at": str(value.get("created_at") or datetime.now(timezone.utc).isoformat()),
            "created_ts": self.float_value(value.get("created_ts"), time.time()),
            "created_by": str(value.get("created_by") or "yoagent"),
            "status": status,
            "last_observed_state": dict(value.get("last_observed_state") if isinstance(value.get("last_observed_state"), dict) else {}),
            "timeout_at": str(value.get("timeout_at") or ""),
            "timeout_ts": self.float_value(value.get("timeout_ts"), 0.0),
            "confirm_required": bool(value.get("confirm_required")),
            "confirmed_at": str(value.get("confirmed_at") or ""),
            "idempotency_key": str(value.get("idempotency_key") or ""),
            "audit_event_ids": [str(item) for item in value.get("audit_event_ids", []) if item],
        }
        for key in ("fired_at", "cancelled_at", "failed_at", "timed_out_at", "result", "error"):
            if key in value:
                job[key] = value[key]
        return job

    def load_yoagent_jobs(self) -> dict[str, dict[str, Any]]:
        raw = read_yolomux_state().get(YOAGENT_JOBS_STATE_KEY, {})
        items = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        jobs: dict[str, dict[str, Any]] = {}
        for item in items:
            job = self.sanitize_yoagent_job(item)
            if job:
                jobs[str(job["id"])] = job
        return jobs

    def persist_yoagent_jobs_locked(self) -> None:
        jobs = dict(sorted(self.yoagent_jobs.items(), key=lambda item: float(item[1].get("created_ts") or 0), reverse=True)[:YOAGENT_JOB_MAX_ITEMS])
        self.yoagent_jobs = jobs
        update_yolomux_state({YOAGENT_JOBS_STATE_KEY: jobs})

    def publish_yoagent_jobs_changed(self, reason: str, job: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"reason": reason}
        if job:
            payload["job"] = self.public_yoagent_job(job)
            notification = job.get("notification") if isinstance(job.get("notification"), dict) else {}
            if notification:
                payload["notification"] = notification
        self.publish_client_event("yoagent_jobs_changed", payload, trigger=reason, cache="ready")

    def public_yoagent_job(self, job: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(job)
        prompt = str(public.get("prompt") or "")
        if prompt:
            public["prompt_preview"] = redacted_action_preview(prompt)
            public["prompt"] = redacted_action_text(prompt, 240)
            public["public_text"] = redacted_action_text(prompt, 240)
        action = public.get("action") if isinstance(public.get("action"), dict) else {}
        if "text" in action:
            action["text_preview"] = redacted_action_preview(str(action.get("text") or ""))
            action["text"] = redacted_action_text(str(action.get("text") or ""), 240)
        public["action"] = action
        return public

    def public_yoagent_action_preview(self, preview: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(preview)
        if "text" in public:
            public["text_preview"] = redacted_action_preview(str(public.get("text") or ""))
            public["text"] = redacted_action_text(str(public.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        screen = public.get("screen") if isinstance(public.get("screen"), dict) else {}
        if "detected_text" in screen:
            detected = str(screen.get("detected_text") or "")
            screen["detected_text_preview"] = redacted_action_preview(detected)
            screen["detected_text"] = redacted_action_text(detected, 240)
            public["screen"] = screen
        handoff = public.get("handoff") if isinstance(public.get("handoff"), dict) else {}
        if "instruction" in handoff:
            handoff["instruction"] = redacted_action_text(str(handoff.get("instruction") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        if handoff:
            public["handoff"] = handoff
        return public

    def yoagent_jobs_payload(self) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            jobs = [self.public_yoagent_job(job) for job in self.yoagent_jobs.values()]
        jobs.sort(key=lambda item: float(item.get("created_ts") or 0), reverse=True)
        return {"ok": True, "jobs": jobs}, HTTPStatus.OK

    def yoagent_job_idempotency_key(self, job_type: str, target: dict[str, Any], predicate: dict[str, Any], action: dict[str, Any]) -> str:
        signature = self.client_event_payload_signature({
            "type": job_type,
            "target": target,
            "predicate": predicate,
            "action": action,
        })
        return hashlib.sha256(signature.encode("utf-8", errors="replace")).hexdigest()

    def yoagent_job_spec_from_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        raw_type = str(payload.get("type") or payload.get("kind") or "").strip()
        job_type = raw_type or "notify_session_idle"
        target_payload = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        action_payload = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        session = str(payload.get("session") or target_payload.get("session") or action_payload.get("session") or "").strip()
        if job_type in {"notify_when_idle", "notify_session", "notify_session_idle"}:
            job_type = "notify_session_idle"
            if not session:
                return {"error": "missing target session"}, HTTPStatus.BAD_REQUEST
            unknown = self.require_known_session(session)
            if unknown:
                return unknown
            target = {"session": session}
            predicate = {"type": "session_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = {"type": "notify_user", "message": str(payload.get("message") or f"tmux session `{session}` is idle")}
        elif job_type in {"notify_all_idle", "all_idle_summary"}:
            roster = [str(item) for item in (payload.get("roster") if isinstance(payload.get("roster"), list) else self.sessions) if str(item)]
            missing = [item for item in roster if item not in self.sessions]
            if missing:
                return {"error": "unknown sessions in roster", "sessions": missing}, HTTPStatus.NOT_FOUND
            target = {"roster": roster}
            predicate = {"type": "all_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = {"type": "notify_user", "message": str(payload.get("message") or "all watched tmux sessions are idle")}
        elif job_type in {"wait_then_send", "wait_then_run"}:
            job_type = "wait_then_send"
            if not session:
                return {"error": "missing target session"}, HTTPStatus.BAD_REQUEST
            unknown = self.require_known_session(session)
            if unknown:
                return unknown
            text = truncate_text(str(payload.get("text") or action_payload.get("text") or "").strip(), YOAGENT_ACTION_TEXT_LIMIT)
            if not text:
                return {"session": session, "error": "missing prompt text"}, HTTPStatus.BAD_REQUEST
            target = {"session": session}
            predicate = {"type": "session_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = {"type": "send_prompt", "session": session, "text": text, "submit": action_payload.get("submit") is not False, "return_result": bool(action_payload.get("return_result") or payload.get("return_result"))}
        else:
            return {"error": f"unsupported YO!agent job type: {job_type}"}, HTTPStatus.BAD_REQUEST
        return {"type": job_type, "target": target, "predicate": predicate, "action": action}, HTTPStatus.OK

    def create_yoagent_job(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        spec, status = self.yoagent_job_spec_from_payload(payload)
        if status != HTTPStatus.OK:
            return spec, status
        job_type = str(spec["type"])
        target = spec["target"]
        predicate = spec["predicate"]
        action = spec["action"]
        risk_labels = self.yoagent_action_risk_labels(str(action.get("text") or "")) if action.get("type") == "send_prompt" else []
        if risk_labels:
            action["risk_labels"] = risk_labels
        confirm_required = bool(payload.get("confirm_required") or payload.get("requires_confirmation") or risk_labels)
        idempotency_key = str(payload.get("idempotency_key") or self.yoagent_job_idempotency_key(job_type, target, predicate, action))
        with self.yoagent_job_lock:
            for existing in self.yoagent_jobs.values():
                if existing.get("idempotency_key") == idempotency_key and existing.get("status") in {"queued", "pending_confirmation"}:
                    return {"ok": False, "duplicate": True, "job": self.public_yoagent_job(existing)}, HTTPStatus.CONFLICT
            now_ts = time.time()
            timeout_minutes = max(1.0, min(1440.0, self.float_value(payload.get("timeout_minutes"), YOAGENT_JOB_DEFAULT_TIMEOUT_MINUTES)))
            timeout_dt = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
            prompt = self.yoagent_job_prompt_text(action)
            job_id = f"yj_{uuid.uuid4().hex[:16]}"
            job = {
                "id": job_id,
                "job_id": job_id,
                "type": job_type,
                "target": target,
                "predicate": predicate,
                "action": action,
                "transport": self.yoagent_job_transport_id(target, action),
                "prompt": prompt,
                "prompt_preview": redacted_action_preview(prompt),
                "public_text": redacted_action_text(prompt, 240),
                "started_at": "",
                "result_marker": {},
                "result_source": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_ts": now_ts,
                "created_by": "yoagent",
                "status": "pending_confirmation" if confirm_required else "queued",
                "last_observed_state": {},
                "timeout_at": timeout_dt.isoformat(),
                "timeout_ts": now_ts + timeout_minutes * 60.0,
                "confirm_required": confirm_required,
                "confirmed_at": "" if confirm_required else datetime.now(timezone.utc).isoformat(),
                "idempotency_key": idempotency_key,
                "audit_event_ids": [],
            }
            self.yoagent_jobs[job["id"]] = job
            self.persist_yoagent_jobs_locked()
        event = self.log_event(str(target.get("session") or ""), "yoagent_job_created", f"YO!agent job created: {job_type}", {
            "job_id": job["id"],
            "type": job_type,
            "target_session": target.get("session", ""),
            "roster": target.get("roster", []),
            "predicate": predicate.get("type", ""),
            "action": action.get("type", ""),
            "text_preview": redacted_action_preview(str(action.get("text") or "")),
            "risk_labels": risk_labels,
        })
        with self.yoagent_job_lock:
            current = self.yoagent_jobs.get(job["id"])
            if current is not None:
                current["audit_event_ids"].append(str(event.get("time") or ""))
                self.persist_yoagent_jobs_locked()
                job = copy.deepcopy(current)
        self.publish_yoagent_jobs_changed("yoagent_job_created", job)
        return {"ok": True, "job": self.public_yoagent_job(job)}, HTTPStatus.OK

    def confirm_yoagent_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(str(job_id))
            if not job:
                return {"error": "job not found"}, HTTPStatus.NOT_FOUND
            if job.get("status") != "pending_confirmation":
                return {"ok": True, "job": self.public_yoagent_job(job)}, HTTPStatus.OK
            job["status"] = "queued"
            job["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            self.persist_yoagent_jobs_locked()
            public = copy.deepcopy(job)
        self.log_event(str(public.get("target", {}).get("session") or ""), "yoagent_job_confirmed", f"YO!agent job confirmed: {job_id}", {"job_id": job_id})
        self.publish_yoagent_jobs_changed("yoagent_job_confirmed", public)
        self.client_watch_wake_event.set()
        return {"ok": True, "job": self.public_yoagent_job(public)}, HTTPStatus.OK

    def cancel_yoagent_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(str(job_id))
            if not job:
                return {"error": "job not found"}, HTTPStatus.NOT_FOUND
            if job.get("status") in {"fired", "cancelled", "failed", "timed_out"}:
                return {"ok": True, "job": self.public_yoagent_job(job)}, HTTPStatus.OK
            job["status"] = "cancelled"
            job["cancelled_at"] = datetime.now(timezone.utc).isoformat()
            self.persist_yoagent_jobs_locked()
            public = copy.deepcopy(job)
        self.log_event(str(public.get("target", {}).get("session") or ""), "yoagent_job_cancelled", f"YO!agent job cancelled: {job_id}", {"job_id": job_id})
        self.publish_yoagent_jobs_changed("yoagent_job_cancelled", public)
        return {"ok": True, "job": self.public_yoagent_job(public)}, HTTPStatus.OK

    def yoagent_intent(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else payload
        intent_type = str(intent.get("type") or "")
        if intent_type in {"send_prompt", "wait_then_send", "session_handoff"}:
            preview, status = self.create_yoagent_action_preview(intent)
            risk = "mutating-send" if str(preview.get("status") or "") == "ready" else "waiting-target"
            return {"ok": status == HTTPStatus.OK, "intent": intent, "preview": preview, "risk": risk, "confirmation_required": bool(preview.get("requires_confirmation"))}, status
        job, status = self.yoagent_job_spec_from_payload(intent)
        return {"ok": status == HTTPStatus.OK, "intent": intent, "job_preview": job, "risk": "mutating-job" if (job.get("action") or {}).get("type") == "send_prompt" else "notify-only"}, status

    def yoagent_job_observed_state(self, job: dict[str, Any]) -> dict[str, Any]:
        predicate = job.get("predicate") if isinstance(job.get("predicate"), dict) else {}
        target = job.get("target") if isinstance(job.get("target"), dict) else {}
        predicate_type = str(predicate.get("type") or "")
        if predicate_type == "all_idle":
            roster = [str(item) for item in target.get("roster", []) if str(item)]
            blockers: list[str] = []
            states: dict[str, str] = {}
            for session in roster:
                current, status = self.yoagent_action_target(session)
                if status != HTTPStatus.OK:
                    states[session] = "missing"
                    blockers.append(session)
                    continue
                screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
                state_key = str(screen.get("key") or "idle")
                states[session] = state_key
                if state_key not in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS:
                    blockers.append(session)
            return {"ready": not blockers and bool(roster), "state": "all_idle" if not blockers else "waiting", "states": states, "blockers": blockers}
        session = str(target.get("session") or "").strip()
        current, status = self.yoagent_action_target(session)
        if status != HTTPStatus.OK:
            return {"ready": False, "state": "missing", "error": current.get("error") or status.phrase}
        accepting, acceptance_text = self.yoagent_action_acceptance(current)
        screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
        state_key = str(screen.get("key") or ("idle" if accepting else "waiting"))
        ready = accepting if predicate_type in {"session_idle", "session_done"} else False
        return {"ready": ready, "state": state_key, "acceptance_text": acceptance_text, "target": current}

    def update_yoagent_job_observation(self, job_id: str, observed: dict[str, Any], now: float) -> tuple[dict[str, Any] | None, bool]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(job_id)
            if not job or job.get("status") != "queued":
                return None, False
            predicate = job.get("predicate") if isinstance(job.get("predicate"), dict) else {}
            quiet_seconds = max(0.0, self.float_value(predicate.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS))
            previous = job.get("last_observed_state") if isinstance(job.get("last_observed_state"), dict) else {}
            state = str(observed.get("state") or "")
            ready = bool(observed.get("ready"))
            if previous.get("state") != state or bool(previous.get("ready")) != ready:
                since = now
            else:
                since = self.float_value(previous.get("since_ts"), now)
            job["last_observed_state"] = {
                "state": state,
                "ready": ready,
                "since_ts": since,
                "observed_ts": now,
                "blockers": observed.get("blockers", []),
                "states": observed.get("states", {}),
                "acceptance_text": observed.get("acceptance_text", ""),
            }
            self.persist_yoagent_jobs_locked()
            return copy.deepcopy(job), ready and now - since >= quiet_seconds

    def complete_yoagent_job(self, job_id: str, status: str, result: dict[str, Any]) -> dict[str, Any] | None:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(job_id)
            if not job:
                return None
            job["status"] = status
            timestamp_key = "fired_at" if status == "fired" else "failed_at" if status == "failed" else "timed_out_at"
            timestamp = datetime.now(timezone.utc).isoformat()
            job[timestamp_key] = timestamp
            if status == "fired" and not job.get("started_at"):
                job["started_at"] = timestamp
            if result:
                job["result"] = result
                transport = self.yoagent_job_transport_id(
                    job.get("target") if isinstance(job.get("target"), dict) else {},
                    job.get("action") if isinstance(job.get("action"), dict) else {},
                    result,
                )
                if transport:
                    job["transport"] = transport
                marker = self.yoagent_job_result_marker_from_result(result)
                if marker:
                    job["result_marker"] = marker
                result_source = self.yoagent_job_result_source_from_result(result)
                if result_source:
                    job["result_source"] = result_source
            self.persist_yoagent_jobs_locked()
            return copy.deepcopy(job)

    def fire_yoagent_job(self, job: dict[str, Any], observed: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        action = job.get("action") if isinstance(job.get("action"), dict) else {}
        action_type = str(action.get("type") or "notify_user")
        session = str((job.get("target") if isinstance(job.get("target"), dict) else {}).get("session") or action.get("session") or "")
        if action_type == "notify_user":
            message = str(action.get("message") or f"YO!agent job `{job_id}` fired")
            notification = {"title": "YO!agent", "body": message, "session": session}
            completed = self.complete_yoagent_job(job_id, "fired", {"message": message})
            if completed:
                completed["notification"] = notification
                self.log_event(session, "yoagent_job_fired", message, {"job_id": job_id, "type": job.get("type"), "state": observed.get("state", "")})
                self.publish_yoagent_jobs_changed("yoagent_job_fired", completed)
            return
        if action_type == "send_prompt":
            intent = {
                "type": "send_prompt",
                "session": str(action.get("session") or session),
                "text": str(action.get("text") or ""),
                "submit": action.get("submit") is not False,
                "return_result": bool(action.get("return_result")),
            }
            preview, preview_status = self.create_yoagent_action_preview(intent)
            if preview_status == HTTPStatus.OK and preview.get("status") == "ready":
                result, result_status = self.execute_yoagent_send_action({"preview_id": preview.get("id")}, persist_result=True, start_result_watch=bool(intent.get("return_result")))
                completed = self.complete_yoagent_job(job_id, "fired" if result_status == HTTPStatus.OK else "failed", {"action": preview, "send": result, "status": int(result_status)})
                if completed:
                    self.log_event(str(intent.get("session") or ""), "yoagent_job_fired", f"YO!agent job sent prompt to {intent.get('session')}", {"job_id": job_id, "status": int(result_status), "text_preview": redacted_action_preview(str(intent.get("text") or ""))})
                    self.publish_yoagent_jobs_changed("yoagent_job_fired", completed)
                return
            reason = preview.get("acceptance_text") or preview.get("error") or "target is not ready"
            failed = self.complete_yoagent_job(job_id, "failed", {"error": reason, "action": preview})
            if failed:
                self.log_event(str(intent.get("session") or ""), "yoagent_job_failed", f"YO!agent job failed: {reason}", {"job_id": job_id})
                self.publish_yoagent_jobs_changed("yoagent_job_failed", failed)

    def poll_yoagent_jobs_once(self) -> list[str]:
        now = time.time()
        fired: list[str] = []
        with self.yoagent_job_lock:
            jobs = [copy.deepcopy(job) for job in self.yoagent_jobs.values() if job.get("status") == "queued"]
        for job in jobs:
            job_id = str(job.get("id") or "")
            if self.float_value(job.get("timeout_ts"), 0.0) and now >= self.float_value(job.get("timeout_ts"), 0.0):
                timed_out = self.complete_yoagent_job(job_id, "timed_out", {"reason": "timeout"})
                if timed_out:
                    timed_out["notification"] = {
                        "title": "YO!agent",
                        "body": f"YO!agent job `{job_id}` timed out",
                        "session": str((timed_out.get("target") or {}).get("session") or ""),
                    }
                    self.log_event(str((timed_out.get("target") or {}).get("session") or ""), "yoagent_job_timed_out", f"YO!agent job timed out: {job_id}", {"job_id": job_id})
                    self.publish_yoagent_jobs_changed("yoagent_job_timed_out", timed_out)
                continue
            observed = self.yoagent_job_observed_state(job)
            if str(observed.get("state") or "") == "missing":
                reason = str(observed.get("error") or "target session is missing")
                failed = self.complete_yoagent_job(job_id, "failed", {"error": reason, "observed": observed})
                if failed:
                    failed["notification"] = {
                        "title": "YO!agent",
                        "body": f"YO!agent job `{job_id}` failed: {reason}",
                        "session": str((failed.get("target") or {}).get("session") or ""),
                    }
                    self.log_event(str((failed.get("target") or {}).get("session") or ""), "yoagent_job_failed", f"YO!agent job failed: {reason}", {"job_id": job_id})
                    self.publish_yoagent_jobs_changed("yoagent_job_failed", failed)
                continue
            current, should_fire = self.update_yoagent_job_observation(job_id, observed, now)
            if current and should_fire:
                self.fire_yoagent_job(current, observed)
                fired.append(job_id)
        return fired

    def yoagent_wait_regarding_text(self, text: Any, fallback: str) -> str:
        preview = redacted_action_preview(" ".join(str(text or "").split()), 90).strip(" .")
        return preview or fallback

    def yoagent_action_wait_label(self, preview: dict[str, Any]) -> str:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(preview.get("session") or target.get("session") or "").strip()
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        target_session = str(handoff.get("session") or "").strip()
        if target_session:
            source_regarding = self.yoagent_wait_regarding_text(preview.get("text"), "the current request")
            target_regarding = self.yoagent_wait_regarding_text(handoff.get("instruction"), "the next request")
            return (
                f"Waiting for tmux session `{session}` to respond (regarding {source_regarding}), before handing off "
                f"the next request to tmux session `{target_session}` (regarding {target_regarding})"
            )
        return f"Waiting for tmux session `{session}` to reply"

    def register_yoagent_action_wait(self, watch_id: str, preview: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(preview.get("session") or target.get("session") or "").strip()
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        pending = {
            "id": watch_id,
            "session": session,
            "label": self.yoagent_action_wait_label(preview),
            "started_ts": time.time(),
            "wait_seconds": YOAGENT_ACTION_RESULT_WAIT_SECONDS,
            "transcript": str(marker.get("transcript") or ""),
        }
        if handoff:
            pending["handoff"] = {
                "source_session": str(handoff.get("source_session") or session),
                "session": str(handoff.get("session") or ""),
                "source_regarding": self.yoagent_wait_regarding_text(preview.get("text"), "the current request"),
                "target_regarding": self.yoagent_wait_regarding_text(handoff.get("instruction"), "the next request"),
            }
        with self.yoagent_action_lock:
            self.yoagent_action_waits[watch_id] = pending
        self.publish_yoagent_conversation_changed("yoagent_wait_started")
        return pending

    def finish_yoagent_action_wait(self, watch_id: str | None, reason: str = "done") -> None:
        if not watch_id:
            return
        removed = False
        with self.yoagent_action_lock:
            removed = self.yoagent_action_waits.pop(watch_id, None) is not None
        if removed:
            self.publish_yoagent_conversation_changed(reason)

    def yoagent_prompt_history(self, raw_history: Any, question: str) -> list[dict[str, str]]:
        source = raw_history if isinstance(raw_history, list) and raw_history else yoagent_conversation.load_messages()
        sanitized = [message for message in (yoagent_conversation.sanitize_message(item) for item in source) if message is not None]
        if sanitized and sanitized[-1].get("role") == "user":
            latest = " ".join(str(sanitized[-1].get("content") or "").split())
            if latest == question:
                sanitized = sanitized[:-1]
        history: list[dict[str, str]] = []
        for item in sanitized[-8:]:
            role = "user" if item.get("role") == "user" else "assistant"
            content = truncate_text(" ".join(str(item.get("content") or "").split()), 1000)
            if content:
                history.append({"role": role, "content": content})
        return history

    def yoagent_activity_payload(self) -> dict[str, Any]:
        payload = dict(self.activity_summary_payload())
        payload["yoagent_skills"] = self.yoagent_skills_payload()
        return payload

    def prune_yoagent_action_previews(self) -> None:
        cutoff = time.time() - YOAGENT_ACTION_PREVIEW_TTL_SECONDS
        with self.yoagent_action_lock:
            for preview_id, preview in list(self.yoagent_action_previews.items()):
                if float(preview.get("created_ts") or 0) < cutoff:
                    self.yoagent_action_previews.pop(preview_id, None)

    def yoagent_managed_target_metadata(self, session: str, agent: Any | None) -> dict[str, Any]:
        candidates = [str(session or "").strip()]
        if agent is not None:
            session_id = str(agent.session_id or "").strip()
            pane_target = str(agent.pane_target or "").strip()
            if session_id:
                candidates.append(session_id)
                candidates.append(f"{session}:{session_id}")
            if pane_target:
                candidates.append(pane_target)
                candidates.append(f"{session}:{pane_target}")
        for key in candidates:
            if key and key in self.yoagent_managed_targets:
                value = self.yoagent_managed_targets[key]
                return dict(value) if isinstance(value, dict) else {}
        return {}

    def yoagent_target_with_transport(self, target: dict[str, Any]) -> dict[str, Any]:
        transport_provider = self.yoagent_transports.first_available(target)
        return {
            **target,
            "transport": transport_provider.id,
            "transport_label": transport_provider.label,
            "transport_kind": transport_provider.kind,
            "transport_capabilities": list(transport_provider.capabilities),
        }

    @requires_known_session(refresh=True)
    def yoagent_action_target(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        discovered, errors = discover_sessions([session])
        info = discovered.get(session)
        if info is None:
            return {"session": session, "error": "session metadata unavailable", "errors": errors}, HTTPStatus.NOT_FOUND
        selected = info.selected_pane
        agent = None
        if selected:
            agent = next((item for item in info.agents if item.pane_target == selected.target), None)
        if agent is None:
            agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        target_pane = agent.pane_target if agent and agent.pane_target else (selected.target if selected else tmux_session_target(session))
        prompt, screen = self.yoagent_action_pane_status(session, target_pane, discovered_sessions=discovered)
        target = {
            "session": session,
            "pane_target": target_pane,
            "pane_window": selected.window if selected else "",
            "pane_index": selected.pane if selected else "",
            "cwd": (agent.cwd if agent and agent.cwd else selected.current_path if selected else "") or "",
            "agent_kind": agent.kind if agent else "",
            "agent_session_id": agent.session_id if agent else "",
            "agent_model": agent.model if agent else "",
            "agent_transcript": agent.transcript if agent and agent.transcript else "",
            "prompt": prompt,
            "screen": screen,
            "errors": errors,
        }
        managed_metadata = self.yoagent_managed_target_metadata(session, agent)
        if managed_metadata:
            target.update(managed_metadata)
            target["managed"] = managed_metadata.get("managed") is not False
        return self.yoagent_target_with_transport(target), HTTPStatus.OK

    def yoagent_action_pane_status(self, session: str, target_pane: str, discovered_sessions: dict[str, SessionInfo] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            visible_text = tmux_capture_pane(target_pane, visible_only=True)
            if visible_text is None:
                prompt = normalized_prompt_state()
                prompt["error"] = "failed to capture pane"
                return prompt, {"key": "disconnected", "text": "failed to capture pane"}
            prompt_state = hybrid_approval_prompt_state(session, visible_text, prompt_source=self.auto_approve_prompt_source())
            if prompt_state.get("visible") and prompt_state.get("type") == "bash":
                pane_text = tmux_capture_pane(target_pane)
                prompt_state = hybrid_approval_prompt_state(session, visible_text, pane_text or visible_text, prompt_source=self.auto_approve_prompt_source())
            screen_state = agent_screen_state(visible_text)
            composer_text = self.yoagent_visible_composer_text(visible_text)
            if screen_state.get("key") == "idle" and composer_text:
                screen_state = {
                    "key": "input-draft",
                    "text": "target input box already contains unsent text",
                    "detected_text": composer_text,
                }
            if screen_state.get("key") == "idle":
                infos = discovered_sessions
                if infos is None:
                    infos, _errors = discover_sessions([session])
                transcript_state = session_transcript_activity_state(infos.get(session))
                if transcript_state.get("key") != "idle":
                    screen_state = transcript_state
            return normalized_prompt_state(prompt_state), dict(screen_state)
        except (OSError, subprocess.SubprocessError) as exc:
            prompt = normalized_prompt_state()
            prompt["error"] = str(exc)
            return prompt, {"key": "error", "text": str(exc)}

    def yoagent_action_acceptance(self, target: dict[str, Any]) -> tuple[bool, str]:
        agent_kind = str(target.get("agent_kind") or "").strip().lower()
        if agent_kind not in YOAGENT_ACTION_AGENT_KINDS:
            return False, "target pane does not have a detected Claude or Codex agent"
        if not str(target.get("pane_target") or "").strip():
            return False, "target pane is missing"
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        if prompt.get("visible"):
            return False, "target agent is at an approval prompt, not a fresh AI prompt"
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "idle")
        if screen_key == "input-draft":
            return True, "target input box has unsent text; YO!agent will clear it before sending"
        if screen_key in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS:
            return True, "target agent is accepting an AI prompt"
        if screen_key == "working":
            return False, "target agent is still working"
        if screen_key in {"approval", "needs-approval", "yolo-approval"}:
            return False, "target agent is at an approval prompt"
        if screen_key in {"disconnected", "error"}:
            return False, str(screen.get("text") or "target pane is not reachable")
        return False, f"target agent is not accepting an AI prompt ({screen_key})"

    def yoagent_action_risk_labels(self, text: str) -> list[str]:
        value = str(text or "")
        checks = [
            ("secret-like-text", r"(?i)\b(?:token|secret|password|api[_-]?key)\s*=\s*\S+"),
            ("credential-path", r"(?i)(~?/\.config/gitlab-token|~?/\.cache/huggingface/token|~?/\.docker/config\.json|~?/\.ngc/config)"),
            ("recursive-delete", r"(?i)\brm\s+-[^\n]*r[^\n]*f\b"),
            ("hard-reset", r"(?i)\bgit\s+reset\s+--hard\b"),
            ("broad-pkill", r"(?i)\bpkill\s+-f\b"),
            ("recursive-permission-change", r"(?i)\b(?:chmod|chown)\s+-R\b"),
            ("mfa-ssh", r"(?i)\bssh\s+\S+"),
        ]
        return [label for label, pattern in checks if re.search(pattern, value)]

    def create_yoagent_action_preview(self, intent: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        session = str(intent.get("session") or "").strip()
        text = truncate_text(str(intent.get("text") or "").strip(), YOAGENT_ACTION_TEXT_LIMIT)
        if not session:
            return {"error": "missing target session"}, HTTPStatus.BAD_REQUEST
        if not text:
            return {"session": session, "error": "missing prompt text"}, HTTPStatus.BAD_REQUEST
        target, status = self.yoagent_action_target(session)
        if status != HTTPStatus.OK:
            return target, status
        accepting, acceptance_text = self.yoagent_action_acceptance(target)
        preview_id = f"ya_{secrets.token_urlsafe(12)}"
        risk_labels = self.yoagent_action_risk_labels(text)
        requires_confirmation = bool(intent.get("requires_confirmation") or risk_labels)
        preview = {
            "id": preview_id,
            "type": str(intent.get("type") or "send_prompt"),
            "session": session,
            "text": text,
            "submit": intent.get("submit") is not False,
            "requires_confirmation": requires_confirmation,
            "return_result": bool(intent.get("return_result")),
            "risk_labels": risk_labels,
            "status": "ready" if accepting else "waiting",
            "status_text": "ready to send now" if accepting else acceptance_text,
            "target": {key: target.get(key) for key in [
                "session",
                "pane_target",
                "pane_window",
                "pane_index",
                "cwd",
                "agent_kind",
                "agent_session_id",
                "agent_model",
                "agent_transcript",
                "transport",
                "transport_label",
                "transport_kind",
                "transport_capabilities",
            ]},
            "screen": target.get("screen") or {},
            "accepting_prompt": accepting,
            "acceptance_text": acceptance_text,
            "created_ts": time.time(),
            "expires_in_seconds": YOAGENT_ACTION_PREVIEW_TTL_SECONDS,
        }
        if isinstance(intent.get("handoff"), dict):
            handoff = intent["handoff"]
            preview["handoff"] = {
                "source_session": str(handoff.get("source_session") or session),
                "session": str(handoff.get("session") or ""),
                "instruction": truncate_text(str(handoff.get("instruction") or ""), YOAGENT_ACTION_TEXT_LIMIT),
            }
        if accepting:
            with self.yoagent_action_lock:
                self.yoagent_action_previews[preview_id] = copy.deepcopy(preview)
        self.log_event(session, "yoagent_action_preview", f"YO!agent previewed send to {session}", {
            "preview_id": preview_id,
            "type": preview["type"],
            "transport": preview["target"].get("transport"),
            "status": preview["status"],
            "risk_labels": risk_labels,
            "text_preview": redacted_action_preview(text),
        })
        return self.public_yoagent_action_preview(preview), HTTPStatus.OK

    def yoagent_action_answer(self, action: dict[str, Any]) -> str:
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        session = str(action.get("session") or target.get("session") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_label = str(target.get("transport_label") or self.yoagent_transports.get(transport).label)
        cwd = str(target.get("cwd") or "").strip()
        action_text = str(action.get("text") or "")
        if action.get("status") == "ready":
            where = f" in `{cwd}`" if cwd else ""
            screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
            clear_note = " I will clear the existing target input before sending." if str(screen.get("key") or "") == "input-draft" else ""
            return "\n".join([
                f"I resolved tmux session `{session}`{where} and prepared a confirmed send action.{clear_note}",
                "",
                f"Transport: `{transport_label}`. Text to send:",
                "",
                f"```text\n{action_text}\n```",
            ])
        screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
        if str(screen.get("key") or "") == "input-draft":
            detected = str(screen.get("detected_text_preview") or screen.get("detected_text") or "").strip()
            if detected:
                return f"I resolved tmux session `{session}`, but I did not send anything because the target input box already contains unsent text: `{detected}`."
        return f"I resolved tmux session `{session}`, but I did not send anything because {action.get('acceptance_text') or 'the target is not accepting an AI prompt'}."

    def yoagent_action_preview_details(self, action: dict[str, Any]) -> str:
        lines: list[str] = []
        session = str(action.get("session") or "")
        screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "").strip()
        if session:
            lines.append(f"- target session: `{session}`")
        if screen_key:
            lines.append(f"- target screen state: `{screen_key}`")
        if screen_key == "input-draft":
            detected = str(screen.get("detected_text_preview") or screen.get("detected_text") or "").strip()
            if detected:
                lines.append(f"- detected target composer text: `{detected}`")
        acceptance = str(action.get("acceptance_text") or "").strip()
        if acceptance:
            lines.append(f"- send blocker: {acceptance}")
        return "\n".join(lines)

    def yoagent_action_sent_answer(self, preview: dict[str, Any], result: dict[str, Any]) -> str:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(result.get("session") or preview.get("session") or target.get("session") or "")
        agent = " ".join(str(item) for item in [target.get("agent_kind"), target.get("agent_model")] if item)
        agent_text = f" ({agent})" if agent else ""
        transport_label = str(result.get("transport_label") or target.get("transport_label") or self.yoagent_transports.get(str(target.get("transport") or "")).label)
        prompt_text = redacted_action_text(str(preview.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        lines = [f"I verified tmux session `{session}`{agent_text} is accepting an AI prompt."]
        if result.get("cleared_input"):
            lines.append("I cleared existing target input first.")
        lines.extend([
            f"I am sending this exact prompt through `{transport_label}`:",
            "",
            f"```text\n{prompt_text}\n```",
        ])
        if preview.get("return_result") or result.get("return_result"):
            lines.append("I am awaiting the response and I'll show the result here when it replies.")
        return "\n".join(lines)

    def yoagent_job_answer(self, job: dict[str, Any]) -> str:
        job_type = str(job.get("type") or "")
        target = job.get("target") if isinstance(job.get("target"), dict) else {}
        action = job.get("action") if isinstance(job.get("action"), dict) else {}
        status = str(job.get("status") or "")
        if status == "pending_confirmation":
            return f"I created YO!agent job `{job.get('id')}` and it is waiting for confirmation."
        if job_type == "notify_all_idle":
            roster = ", ".join(f"`{item}`" for item in target.get("roster", []))
            return f"I created YO!agent job `{job.get('id')}` to notify you when all watched tmux sessions are idle: {roster}."
        if job_type == "wait_then_send":
            return f"I created YO!agent job `{job.get('id')}` to wait for tmux session `{target.get('session')}` to accept an AI prompt, then send:\n\n```text\n{action.get('text') or ''}\n```"
        return f"I created YO!agent job `{job.get('id')}` to notify you when tmux session `{target.get('session')}` is idle."

    def yoagent_action_result_marker(self, target: dict[str, Any]) -> dict[str, Any]:
        transcript = str(target.get("agent_transcript") or "").strip()
        marker: dict[str, Any] = {
            "transcript": transcript,
            "size": 0,
            "mtime_ns": 0,
            "started_ts": time.time(),
        }
        if not transcript:
            return marker
        try:
            stat = Path(transcript).expanduser().stat()
        except OSError:
            return marker
        marker["size"] = int(stat.st_size)
        marker["mtime_ns"] = int(stat.st_mtime_ns)
        return marker

    def yoagent_transcript_delta_text(self, marker: dict[str, Any]) -> str:
        transcript = str(marker.get("transcript") or "").strip()
        if not transcript:
            return ""
        try:
            path = Path(transcript).expanduser()
            start_size = max(0, int(marker.get("size") or 0))
            stat = path.stat()
            with path.open("rb") as handle:
                if stat.st_size >= start_size:
                    handle.seek(start_size)
                data = handle.read(256_000)
        except (OSError, ValueError):
            return ""
        return data.decode("utf-8", errors="replace")

    def yoagent_action_result_text_from_transcript_delta(self, text: str) -> str:
        if not text.strip():
            return ""
        delta_fragments: list[str] = []
        for raw_line in text.splitlines():
            try:
                raw = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            piece = codex_event_text(raw)
            if piece:
                delta_fragments.append(piece)
            payload = raw.get("payload")
            if isinstance(payload, dict):
                piece = codex_event_text(payload)
                if piece:
                    delta_fragments.append(piece)
        items = compact_transcript_items(text, 120)
        for role in ("assistant", "task_complete"):
            for item in reversed(items):
                if item.get("role") != role:
                    continue
                value = str(item.get("text") or "").strip()
                if value:
                    return truncate_text(value, YOAGENT_ACTION_RESULT_MAX_CHARS)
        joined = "".join(delta_fragments).strip()
        return truncate_text(joined, YOAGENT_ACTION_RESULT_MAX_CHARS) if joined else ""

    def yoagent_action_visible_result_text(self, target: dict[str, Any]) -> str:
        pane_target = str(target.get("pane_target") or target.get("session") or "").strip()
        if not pane_target:
            return ""
        try:
            visible_text = tmux_capture_pane(pane_target, visible_only=True)
        except (OSError, subprocess.SubprocessError):
            return ""
        lines = [line.rstrip() for line in str(visible_text or "").splitlines() if line.strip()]
        return truncate_text("\n".join(lines[-40:]), YOAGENT_ACTION_RESULT_MAX_CHARS)

    def record_yoagent_action_result(
        self,
        preview: dict[str, Any],
        text: str,
        *,
        timed_out: bool = False,
        partial: bool = False,
    ) -> dict[str, Any] | None:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(preview.get("session") or target.get("session") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_label = str(target.get("transport_label") or self.yoagent_transports.get(transport).label)
        source_label = f"tmux session `{session}`" if transport == TMUX_LEGACY_TRANSPORT_ID else f"{transport_label} target `{session}`"
        if timed_out and not text:
            content = f"I sent the request to {source_label}, but I did not see a result before the wait timed out."
        else:
            heading = "Partial result" if partial else "Result"
            content = f"{heading} from {source_label}:\n\n{text.strip()}"
        message = self.record_yoagent_message(
            "assistant",
            content,
            created_at=datetime.now(timezone.utc).isoformat(),
            kind="agent_result",
            session=session,
        )
        self.publish_yoagent_conversation_changed("yoagent_result")
        self.log_event(session, "yoagent_action_result", f"YO!agent recorded result from {session}", {
            "timed_out": timed_out,
            "partial": partial,
            "chars": len(text or ""),
            "text_preview": redacted_action_preview(text or ""),
        })
        return message

    def yoagent_handoff_time_add_prompt(self, instruction: str, text: str) -> str:
        add_match = re.search(r"\badd\s+(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\b", instruction, flags=re.IGNORECASE)
        if not add_match or not re.search(r"\b(?:if|whether)\s+that\s+is\s+(?:the\s+)?(?:correct|right)(?:\s+time)?(?:\s+now)?\b", instruction, flags=re.IGNORECASE):
            return ""
        time_match = re.search(
            r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\w{3,9})?\s+)?"
            r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<ampm>[ap]\.?\s*m\.?)?\b",
            text,
            flags=re.IGNORECASE,
        )
        if not time_match:
            return ""
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
        second = int(time_match.group("second") or 0)
        ampm = re.sub(r"[^apm]", "", str(time_match.group("ampm") or "").lower())
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59 or second > 59:
            return ""
        amount = int(add_match.group(1))
        unit = add_match.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith(("h", "hr")) else timedelta(minutes=amount)
        adjusted = datetime(2000, 1, 1, hour, minute, second) + delta
        display_hour = adjusted.hour % 12 or 12
        suffix = "AM" if adjusted.hour < 12 else "PM"
        return f"Is {display_hour}:{adjusted.minute:02d} {suffix} the correct time now?"

    def yoagent_derived_handoff_prompt(self, instruction: str, text: str) -> str:
        return self.yoagent_handoff_time_add_prompt(instruction, text)

    def yoagent_source_neutral_handoff_instruction(self, instruction: str) -> str:
        clean = str(instruction or "").strip(" ,.")
        clean = re.sub(r"\bthat\s+result\b", "the context", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\bthat\s+(?:response|answer|reply)\b", "the context", clean, flags=re.IGNORECASE)
        return clean.strip(" ,.") or "answer the user's follow-up using the context"

    def yoagent_handoff_prompt(self, preview: dict[str, Any], text: str) -> str:
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        target_session = str(handoff.get("session") or "").strip()
        instruction = str(handoff.get("instruction") or "").strip()
        if target_session:
            target = re.escape(target_session)
            instruction = re.sub(rf"\band\s+ask\s+(?:tmux\s+)?session\s+[`'\"]?{target}[`'\"]?\s+if\b", "and say if", instruction, flags=re.IGNORECASE)
            instruction = re.sub(rf"\bask\s+(?:tmux\s+)?session\s+[`'\"]?{target}[`'\"]?\s+", "", instruction, flags=re.IGNORECASE)
        instruction = instruction.strip(" ,.")
        if not instruction:
            instruction = "answer the user's follow-up using that reply"
        derived = self.yoagent_derived_handoff_prompt(instruction, text)
        if derived:
            return truncate_text(derived, YOAGENT_ACTION_TEXT_LIMIT)
        instruction = self.yoagent_source_neutral_handoff_instruction(instruction)
        context = " ".join(text.strip().split())
        return truncate_text(
            f"Use this context: {context} Task: {instruction}.",
            YOAGENT_ACTION_TEXT_LIMIT,
        )

    def continue_yoagent_handoff(self, preview: dict[str, Any], text: str) -> dict[str, Any] | None:
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        next_session = str(handoff.get("session") or "").strip()
        if not next_session or not text.strip():
            return None
        intent = {
            "type": "send_prompt",
            "session": next_session,
            "text": self.yoagent_handoff_prompt(preview, text),
            "submit": True,
            "return_result": True,
        }
        action, action_status = self.create_yoagent_action_preview(intent)
        if action_status == HTTPStatus.OK and action.get("status") == "ready":
            result, result_status = self.execute_yoagent_send_action({"preview_id": action.get("id")}, persist_result=True, start_result_watch=True)
            self.publish_yoagent_conversation_changed("yoagent_handoff")
            return {"ok": result_status == HTTPStatus.OK, "action": action, "result": result, "status": int(result_status)}
        reason = action.get("acceptance_text") or action.get("error") or "the next target is not accepting an AI prompt"
        self.record_yoagent_message(
            "assistant",
            f"I got the result from tmux session `{preview.get('session')}`, but I did not send the handoff to tmux session `{next_session}` because {reason}.",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.publish_yoagent_conversation_changed("yoagent_handoff_blocked")
        return {"ok": False, "action": action, "status": int(action_status), "error": reason}

    def finish_yoagent_action_result(self, preview: dict[str, Any], text: str) -> None:
        self.record_yoagent_action_result(preview, text)
        self.continue_yoagent_handoff(preview, text)

    def run_yoagent_action_result_watcher(
        self,
        preview: dict[str, Any],
        marker: dict[str, Any],
        *,
        watch_id: str | None = None,
        wait_seconds: float = YOAGENT_ACTION_RESULT_WAIT_SECONDS,
        poll_seconds: float = YOAGENT_ACTION_RESULT_POLL_SECONDS,
    ) -> dict[str, Any]:
        try:
            target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
            session = str(preview.get("session") or target.get("session") or "")
            pane_target = str(target.get("pane_target") or session)
            started = time.monotonic()
            deadline = started + max(1.0, float(wait_seconds))
            poll = max(0.1, float(poll_seconds))
            saw_work = False
            last_text = ""
            last_change = started
            pause = threading.Event()
            while time.monotonic() < deadline:
                now = time.monotonic()
                delta_text = self.yoagent_transcript_delta_text(marker)
                delta_state = transcript_delta_result_state(delta_text)
                text = self.yoagent_action_result_text_from_transcript_delta(delta_text)
                if text != last_text:
                    last_text = text
                    last_change = now
                _prompt, screen = self.yoagent_action_pane_status(session, pane_target)
                screen_key = str(screen.get("key") or "idle")
                if screen_key == "working":
                    saw_work = True
                if last_text and delta_state.get("complete") is True:
                    self.finish_yoagent_action_result(preview, last_text)
                    return {"ok": True, "session": session, "source": "transcript", "timed_out": False}
                if (
                    last_text
                    and delta_state.get("has_lifecycle") is not True
                    and delta_state.get("working") is not True
                    and screen_key in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS
                ):
                    self.finish_yoagent_action_result(preview, last_text)
                    return {"ok": True, "session": session, "source": "transcript", "timed_out": False}
                if (
                    last_text
                    and saw_work
                    and now - last_change >= 2.0
                    and delta_state.get("has_lifecycle") is not True
                    and delta_state.get("working") is not True
                ):
                    self.finish_yoagent_action_result(preview, last_text)
                    return {"ok": True, "session": session, "source": "transcript", "timed_out": False}
                pause.wait(min(poll, max(0.0, deadline - time.monotonic())))
            if last_text:
                self.record_yoagent_action_result(preview, last_text, timed_out=True, partial=True)
                return {"ok": True, "session": session, "source": "transcript", "timed_out": True, "partial": True}
            visible_text = self.yoagent_action_visible_result_text(target)
            self.record_yoagent_action_result(preview, visible_text, timed_out=True, partial=bool(visible_text))
            return {"ok": bool(visible_text), "session": session, "source": "screen" if visible_text else "", "timed_out": True}
        finally:
            self.finish_yoagent_action_wait(watch_id, "yoagent_wait_finished")

    def start_yoagent_action_result_watcher(self, preview: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
        watch_id = f"yar_{secrets.token_urlsafe(10)}"
        self.register_yoagent_action_wait(watch_id, preview, marker)
        worker = threading.Thread(
            target=self.run_yoagent_action_result_watcher,
            kwargs={"preview": copy.deepcopy(preview), "marker": copy.deepcopy(marker), "watch_id": watch_id},
            name=f"yoagent-result-{watch_id}",
            daemon=True,
        )
        worker.start()
        return {"id": watch_id, "started": True, "wait_seconds": YOAGENT_ACTION_RESULT_WAIT_SECONDS}

    def yoagent_visible_composer_text(self, visible_text: str) -> str:
        lines = [line.replace("\xa0", " ") for line in str(visible_text or "").splitlines()[-80:]]
        footer_index = -1
        for index in range(len(lines) - 1, -1, -1):
            stripped = lines[index].strip()
            if stripped.startswith(("▶▶", "⏵⏵", "gpt-", "claude ")):
                footer_index = index
                break
        if footer_index < 0:
            return ""
        end_index = footer_index
        for index in range(footer_index - 1, -1, -1):
            if re.match(r"^[─━╌╍]{3,}$", lines[index].strip()):
                end_index = index
                break
        start_index = max(0, end_index - 8)
        for index in range(end_index - 1, -1, -1):
            if re.match(r"^[─━╌╍]{3,}$", lines[index].strip()):
                start_index = index + 1
                break
        lines = lines[start_index:end_index]
        prompt_index = -1
        first_line = ""
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            match = re.match(r"^\s*[❯›>]\s+(?P<text>\S.*)$", line)
            if not match:
                continue
            if re.match(r"^\s*[❯›>]\s*\d+[.:]\s+\S", line):
                continue
            prompt_index = index
            first_line = match.group("text").strip()
            break
        if prompt_index < 0 or not first_line:
            return ""
        block = [first_line]
        for line in lines[prompt_index + 1:]:
            stripped = line.strip()
            if re.match(r"^[─━╌╍]{3,}$", stripped):
                break
            if stripped.startswith(("▶▶", "⏵⏵", "new task?", "gpt-", "claude ")):
                break
            if stripped.startswith(("●", "✻", "⎿", "⤷")) or re.match(r"^(Ran|Bash|Write|Read|Edit|Update|Search)\b", stripped):
                return ""
            block.append(stripped)
        return " ".join(part for part in block if part).strip()

    def yoagent_text_still_in_composer(self, target: dict[str, Any], text: str, wait_seconds: float = 0.8, poll_seconds: float = 0.1) -> bool:
        pane_target = str(target.get("pane_target") or target.get("session") or "").strip()
        needle = " ".join(str(text or "").split())
        if not pane_target or not needle:
            return False
        deadline = time.monotonic() + max(0.0, wait_seconds)
        pause = threading.Event()
        while True:
            try:
                visible_text = tmux_capture_pane(pane_target, visible_only=True) or ""
            except (OSError, subprocess.SubprocessError):
                return False
            pending = " ".join(self.yoagent_visible_composer_text(visible_text).split())
            if not pending:
                return False
            prefix_len = min(120, len(needle))
            if needle in pending or pending in needle or needle[:prefix_len] in pending:
                if time.monotonic() >= deadline:
                    return True
                pause.wait(min(max(0.01, poll_seconds), max(0.0, deadline - time.monotonic())))
                continue
            return False

    def yoagent_clear_target_composer(
        self,
        target: dict[str, Any],
        *,
        wait_seconds: float = 0.8,
        poll_seconds: float = 0.1,
    ) -> dict[str, Any]:
        pane_target = str(target.get("pane_target") or target.get("session") or "").strip()
        if not pane_target:
            return {"ok": False, "cleared": False, "error": "target pane is missing"}
        try:
            visible_text = tmux_capture_pane(pane_target, visible_only=True) or ""
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "cleared": False, "error": str(exc)}
        detected = self.yoagent_visible_composer_text(visible_text)
        if not detected:
            return {"ok": True, "cleared": False, "detected_text": ""}
        result = tmux_clear_input(pane_target)
        if result.returncode != 0:
            return {
                "ok": False,
                "cleared": False,
                "detected_text": detected,
                "error": cmd_error(result, "tmux send-keys C-u failed"),
            }
        deadline = time.monotonic() + max(0.0, wait_seconds)
        pause = threading.Event()
        while True:
            try:
                visible_text = tmux_capture_pane(pane_target, visible_only=True) or ""
            except (OSError, subprocess.SubprocessError) as exc:
                return {"ok": False, "cleared": False, "detected_text": detected, "error": str(exc)}
            remaining = self.yoagent_visible_composer_text(visible_text)
            if not remaining:
                return {"ok": True, "cleared": True, "detected_text": detected}
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "cleared": False,
                    "detected_text": detected,
                    "remaining_text": remaining,
                    "error": "target input box did not clear",
                }
            pause.wait(min(max(0.01, poll_seconds), max(0.0, deadline - time.monotonic())))

    def preview_yoagent_send_action(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        intent = {
            "type": str(payload.get("type") or "send_prompt"),
            "session": str(payload.get("session") or payload.get("target") or ""),
            "text": str(payload.get("text") or payload.get("prompt") or ""),
            "submit": payload.get("submit") is not False,
            "requires_confirmation": payload.get("requires_confirmation") is not False,
            "return_result": bool(payload.get("return_result")),
        }
        return self.create_yoagent_action_preview(intent)

    def execute_yoagent_send_action(
        self,
        payload: dict[str, Any],
        persist_result: bool = True,
        start_result_watch: bool = True,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        self.prune_yoagent_action_previews()
        preview_id = str(payload.get("preview_id") or payload.get("id") or "").strip()
        if not preview_id:
            return {"error": "missing preview_id"}, HTTPStatus.BAD_REQUEST
        with self.yoagent_action_lock:
            preview = copy.deepcopy(self.yoagent_action_previews.get(preview_id) or {})
        if not preview:
            return {"preview_id": preview_id, "error": "action preview expired or unknown"}, HTTPStatus.NOT_FOUND
        if preview.get("status") != "ready":
            return {"preview_id": preview_id, "error": "action is not ready to send"}, HTTPStatus.CONFLICT
        current, status = self.yoagent_action_target(str(preview.get("session") or ""))
        if status != HTTPStatus.OK:
            return current, status
        accepting, acceptance_text = self.yoagent_action_acceptance(current)
        if not accepting:
            return {"preview_id": preview_id, "session": preview.get("session"), "error": acceptance_text}, HTTPStatus.CONFLICT
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        stale_keys = ["pane_target", "agent_kind", "agent_session_id"]
        if any(str(current.get(key) or "") != str(target.get(key) or "") for key in stale_keys):
            return {"preview_id": preview_id, "session": preview.get("session"), "error": "action target changed; create a fresh preview"}, HTTPStatus.CONFLICT
        if normalize_yoagent_transport_id(str(current.get("transport") or "")) != normalize_yoagent_transport_id(str(target.get("transport") or "")):
            return {"preview_id": preview_id, "session": preview.get("session"), "error": "action target changed; create a fresh preview"}, HTTPStatus.CONFLICT
        target = {
            **target,
            "agent_transcript": current.get("agent_transcript") or target.get("agent_transcript") or "",
            "screen": current.get("screen") if isinstance(current.get("screen"), dict) else target.get("screen") or {},
        }
        target["transport"] = normalize_yoagent_transport_id(str(target.get("transport") or current.get("transport") or ""))
        target["transport_label"] = current.get("transport_label") or target.get("transport_label") or self.yoagent_transports.get(str(target.get("transport") or "")).label
        target["transport_kind"] = current.get("transport_kind") or target.get("transport_kind") or self.yoagent_transports.get(str(target.get("transport") or "")).kind
        preview["target"] = target
        text = str(preview.get("text") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_provider = self.yoagent_transports.get(transport)
        return_result = bool(preview.get("return_result") or payload.get("return_result"))
        clear_result: dict[str, Any] = {}
        screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
        if transport == TMUX_LEGACY_TRANSPORT_ID and str(screen.get("key") or "") == "input-draft":
            clear_result = self.yoagent_clear_target_composer(target)
            cleared_text_preview = redacted_action_preview(str(clear_result.get("detected_text") or ""))
            if not clear_result.get("ok"):
                self.log_event(str(preview.get("session") or ""), "yoagent_action_clear_failed", f"YO!agent could not clear input before send to {preview.get('session')}", {
                    "preview_id": preview_id,
                    "transport": transport,
                    "cleared_text_preview": cleared_text_preview,
                    "remaining_text_preview": redacted_action_preview(str(clear_result.get("remaining_text") or "")),
                })
                return {
                    "preview_id": preview_id,
                    "session": preview.get("session"),
                    "transport": transport,
                    "transport_label": transport_provider.label,
                    "sent": False,
                    "cleared_input": False,
                    "cleared_text_preview": cleared_text_preview,
                    "error": clear_result.get("error") or "target input box did not clear",
                }, HTTPStatus.CONFLICT
        result_marker = self.yoagent_action_result_marker(target) if return_result else {}
        send_result = transport_provider.send(
            target,
            text,
            submit=preview.get("submit") is not False,
            tmux_paste_text=tmux_paste_text,
        ).as_dict()
        if not send_result.get("ok"):
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "transport": transport,
                "transport_label": send_result.get("transport_label") or transport_provider.label,
                "error": send_result.get("error") or "transport send failed",
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        if transport == TMUX_LEGACY_TRANSPORT_ID and preview.get("submit") is not False and self.yoagent_text_still_in_composer(target, text):
            with self.yoagent_action_lock:
                self.yoagent_action_previews.pop(preview_id, None)
            error = "pasted text is still in the target input box after Return; target did not submit it"
            self.log_event(str(preview.get("session") or ""), "yoagent_action_unsubmitted", f"YO!agent send to {preview.get('session')} did not submit", {
                "preview_id": preview_id,
                "transport": transport,
                "text_preview": redacted_action_preview(text),
            })
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "transport": transport,
                "transport_label": transport_provider.label,
                "sent": False,
                "pasted": True,
                "error": error,
            }, HTTPStatus.CONFLICT
        with self.yoagent_action_lock:
            self.yoagent_action_previews.pop(preview_id, None)
        self.log_event(str(preview.get("session") or ""), "yoagent_action_executed", f"YO!agent sent action to {preview.get('session')}", {
            "preview_id": preview_id,
            "transport": transport,
            "text_preview": redacted_action_preview(text),
            "cleared_input": bool(clear_result.get("cleared")),
            "cleared_text_preview": redacted_action_preview(str(clear_result.get("detected_text") or "")),
        })
        response = {
            "ok": True,
            "preview_id": preview_id,
            "session": preview.get("session"),
            "transport": transport,
            "transport_label": send_result.get("transport_label") or transport_provider.label,
            "result_source": send_result.get("result_source") or "",
            "answer": "",
            "sent": True,
            "return_result": return_result,
            "result_marker": result_marker,
        }
        if clear_result.get("cleared"):
            response["cleared_input"] = True
            response["cleared_text_preview"] = redacted_action_preview(str(clear_result.get("detected_text") or ""))
        response["answer"] = self.yoagent_action_sent_answer(preview, response)
        if persist_result:
            self.record_yoagent_message(
                "assistant",
                response["answer"],
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            response["conversation"] = self.yoagent_conversation_payload()
        if return_result and send_result.get("text"):
            self.record_yoagent_action_result(preview, str(send_result.get("text") or ""))
            response["result_recorded"] = True
        elif return_result and start_result_watch:
            response["result_watch"] = self.start_yoagent_action_result_watcher(preview, result_marker)
        return response, HTTPStatus.OK

    def reset_yoagent_chat(self) -> dict[str, Any]:
        with self.yoagent_cli_lock:
            self.yoagent_cli_sessions.clear()
            yoagent_conversation.clear_cli_sessions()
        self.close_yoagent_codex_app_server()
        with self.yoagent_action_lock:
            self.yoagent_action_waits.clear()
        yoagent_conversation.clear_messages()
        with self.yoagent_prewarm_lock:
            self.yoagent_prewarm_status = {}
            self.yoagent_startup_response_running = False
        return {"ok": True, "conversation": self.yoagent_conversation_payload()}

    def start_yoagent_backend_prewarm(self, payload: dict[str, Any] | None = None, *, reason: str = "prewarm") -> tuple[dict[str, Any], HTTPStatus]:
        settings = self.yoagent_settings()
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        if backend != "codex" or invocation != "cli":
            return {"ok": True, "started": False, "backend": requested_backend, "backend_used": backend, "reason": "no persistent Codex backend available"}, HTTPStatus.OK
        with self.yoagent_prewarm_lock:
            if self.yoagent_prewarm_running:
                return {"ok": True, "started": False, "backend": requested_backend, "backend_used": backend, "reason": "already running"}, HTTPStatus.ACCEPTED
            self.yoagent_prewarm_running = True
            self.yoagent_prewarm_status = {"backend": backend, "reason": reason, "started_at": time.time()}

        def worker() -> None:
            status: dict[str, Any] = {"backend": backend, "reason": reason}
            try:
                thread_id, fallback_reason, cli_status = self.ensure_yoagent_codex_app_server(settings)
                status = {"backend": backend, "reason": reason, "warmed": bool(thread_id and not fallback_reason), "fallback_reason": fallback_reason, "cli": cli_status}
            except Exception as exc:
                status = {"backend": backend, "reason": reason, "warmed": False, "error": str(exc)}
            finally:
                with self.yoagent_prewarm_lock:
                    self.yoagent_prewarm_status = status
                    self.yoagent_prewarm_running = False

        threading.Thread(target=worker, name="yoagent-prewarm", daemon=True).start()
        return {"ok": True, "started": True, "backend": requested_backend, "backend_used": backend}, HTTPStatus.ACCEPTED

    def yoagent_startup_response(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        if yoagent_conversation.load_messages(limit=1):
            warm_payload, _warm_status = self.start_yoagent_backend_prewarm(payload, reason="startup_existing_conversation")
            return {
                "ok": True,
                "started": False,
                "visible": False,
                "reason": "conversation already has messages",
                "prewarm": warm_payload,
                "conversation": self.yoagent_conversation_payload(),
            }, HTTPStatus.OK
        settings = self.yoagent_settings()
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        locale = str((payload or {}).get("locale") or "en").strip() or "en"
        if backend not in {"codex", "claude"} or invocation != "cli":
            return {
                "ok": True,
                "started": False,
                "visible": False,
                "backend": requested_backend,
                "backend_used": backend,
                "reason": "no CLI backend available",
                "conversation": self.yoagent_conversation_payload(),
            }, HTTPStatus.OK
        with self.yoagent_prewarm_lock:
            if self.yoagent_startup_response_running:
                return {
                    "ok": True,
                    "started": False,
                    "visible": True,
                    "backend": requested_backend,
                    "backend_used": backend,
                    "reason": "startup response already running",
                    "conversation": self.yoagent_conversation_payload(),
                }, HTTPStatus.ACCEPTED
            self.yoagent_startup_response_running = True
        stream_id = f"startup-{uuid.uuid4().hex}"
        started = time.monotonic()
        self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="started")
        try:
            activity_payload = self.yoagent_activity_payload()
            answer, fallback_reason, cli_status = self.run_yoagent_cli_backend(
                backend,
                YOAGENT_STARTUP_QUESTION,
                activity_payload,
                settings,
                [],
                locale,
                stream_id=stream_id,
            )
            model_answered = bool(answer)
            backend_used = backend if model_answered else "deterministic"
            if not answer:
                answer = deterministic_yoagent_reply(YOAGENT_STARTUP_QUESTION, activity_payload, settings, locale)
            response: dict[str, Any] = {
                "ok": True,
                "started": True,
                "visible": True,
                "answer": answer,
                "backend": requested_backend,
                "backend_used": backend_used,
                "fallback": bool(fallback_reason),
                "fallback_reason": fallback_reason,
                "cli": cli_status,
                "timing": {"ttfr_ms": round((time.monotonic() - started) * 1000, 3)},
                "stream_id": stream_id,
                "generated_at": activity_payload.get("generated_at"),
                "session_order": activity_payload.get("session_order", []),
            }
            answer_text, hidden_thinking_removed = strip_yoagent_hidden_thinking(str(response.get("answer") or ""))
            response["answer"] = answer_text
            if hidden_thinking_removed:
                response["hidden_thinking_removed"] = True
            response["details"] = yoagent_response_details(response)
            if answer_text:
                self.record_yoagent_message("assistant", answer_text, details=str(response.get("details") or ""))
                self.publish_yoagent_conversation_changed("yoagent_startup")
            response["conversation"] = self.yoagent_conversation_payload()
            if not model_answered:
                self.publish_yoagent_stream_delta(
                    stream_id,
                    answer_text,
                    backend=backend_used,
                    phase="done",
                    done=True,
                    hidden_thinking_removed=bool(response.get("hidden_thinking_removed")),
                )
            return response, HTTPStatus.OK
        except Exception as exc:
            self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="error", done=True)
            return {"ok": False, "error": str(exc), "stream_id": stream_id, "conversation": self.yoagent_conversation_payload()}, HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            with self.yoagent_prewarm_lock:
                self.yoagent_startup_response_running = False

    def yoagent_prewarm(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        if (payload or {}).get("visible"):
            return self.yoagent_startup_response(payload)
        return self.start_yoagent_backend_prewarm(payload, reason="client_prewarm")

    def yoagent_chat(self, payload: dict[str, Any], access_role: str = "admin") -> tuple[dict[str, Any], HTTPStatus]:
        chat_started = time.monotonic()
        question = truncate_text(" ".join(str(payload.get("message") or payload.get("question") or "").split()), 4000)
        if not question:
            return {"error": "missing YO!agent message"}, HTTPStatus.BAD_REQUEST
        history = self.yoagent_prompt_history(payload.get("history", []), question)
        self.record_yoagent_message("user", question)
        settings = self.yoagent_settings()
        locale = str(payload.get("locale") or "en").strip()
        activity_payload_cache: dict[str, Any] | None = None
        context_lines_cache: list[str] | None = None

        def get_activity_payload() -> dict[str, Any]:
            nonlocal activity_payload_cache
            if activity_payload_cache is None:
                activity_payload_cache = self.yoagent_activity_payload()
            return activity_payload_cache

        def get_context_lines() -> list[str]:
            nonlocal context_lines_cache
            if context_lines_cache is None:
                context_lines_cache = yoagent_context_lines(get_activity_payload())
            return context_lines_cache

        def finish(response: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
            response.setdefault("answered_at", datetime.now(timezone.utc).isoformat())
            timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
            timing.setdefault("ttfr_ms", round((time.monotonic() - chat_started) * 1000, 3))
            response["timing"] = timing
            answer_text = str(response.get("answer") or "")
            answer_text, hidden_thinking_removed = strip_yoagent_hidden_thinking(answer_text)
            response["answer"] = answer_text
            if hidden_thinking_removed:
                response["hidden_thinking_removed"] = True
            details = str(response.get("details") or "").strip()
            generated_details = yoagent_response_details(response)
            response["details"] = "\n".join(part for part in [details, generated_details] if part).strip()
            actions = response.get("actions") if isinstance(response.get("actions"), list) else []
            if answer_text:
                self.record_yoagent_message("assistant", answer_text, actions=actions, details=str(response.get("details") or ""))
            response["conversation"] = self.yoagent_conversation_payload()
            return response, HTTPStatus.OK

        def base_response(
            answer: str,
            *,
            actions: list[dict[str, Any]] | None = None,
            backend: str = "yolomux",
            backend_used: str = "yolomux",
            fallback_reason: str = "",
            details: str = "",
            cli: dict[str, Any] | None = None,
            include_activity: bool = False,
        ) -> dict[str, Any]:
            response_activity = get_activity_payload() if include_activity else {}
            return {
                "answer": answer,
                "actions": actions or [],
                "backend": backend,
                "backend_used": backend_used,
                "fallback": bool(fallback_reason),
                "fallback_reason": fallback_reason,
                "details": details,
                "cli": cli or {},
                "context_lines": get_context_lines() if include_activity else [],
                "generated_at": response_activity.get("generated_at"),
                "session_order": response_activity.get("session_order", []),
            }

        skill_file_intent = parse_yoagent_skill_file_intent(question)
        if skill_file_intent:
            if access_role != "admin":
                return finish(base_response("YO!skill and context file management requires an admin login. I did not change anything."))
            return finish(base_response(self.yoagent_skill_file_answer(skill_file_intent)))

        job_intent = parse_yoagent_job_intent(question, self.sessions)
        if job_intent:
            if access_role != "admin":
                return finish(base_response("YO!agent watch and notify jobs require an admin login. I did not create a job."))
            job_payload, job_status = self.create_yoagent_job(job_intent)
            if job_status not in {HTTPStatus.OK, HTTPStatus.CONFLICT}:
                return {"error": job_payload.get("error") or "failed to create YO!agent job", **job_payload}, job_status
            job = job_payload.get("job") if isinstance(job_payload.get("job"), dict) else {}
            duplicate = " I reused the existing matching job." if job_payload.get("duplicate") else ""
            return finish(base_response(self.yoagent_job_answer(job) + duplicate))

        action_intent = parse_yoagent_action_intent(question, history, self.sessions)
        if action_intent:
            if access_role != "admin":
                return finish(base_response("Sending prompts to tmux sessions through YO!agent requires an admin login. I did not send anything."))
            action, action_status = self.create_yoagent_action_preview(action_intent)
            if action_status != HTTPStatus.OK:
                return {"error": action.get("error") or "failed to create YO!agent action", **action}, action_status
            confirmation_required = bool(action_intent.get("requires_confirmation") or action.get("requires_confirmation"))
            if action.get("status") == "ready" and not confirmation_required:
                result, result_status = self.execute_yoagent_send_action({"preview_id": action.get("id")}, persist_result=False, start_result_watch=False)
                if result_status == HTTPStatus.OK:
                    finished = finish(base_response(self.yoagent_action_sent_answer(action, result)))
                    if action.get("return_result"):
                        self.start_yoagent_action_result_watcher(action, result.get("result_marker") if isinstance(result.get("result_marker"), dict) else {})
                    return finished
                return finish(base_response(f"I did not send anything because {result.get('error') or 'the target is not accepting an AI prompt'}."))
            if action_intent.get("type") == "wait_then_send" and not confirmation_required:
                job_payload, job_status = self.create_yoagent_job({
                    "type": "wait_then_send",
                    "session": action_intent.get("session"),
                    "text": action_intent.get("text"),
                    "return_result": bool(action_intent.get("return_result")),
                })
                if job_status in {HTTPStatus.OK, HTTPStatus.CONFLICT}:
                    job = job_payload.get("job") if isinstance(job_payload.get("job"), dict) else {}
                    duplicate = " I reused the existing matching job." if job_payload.get("duplicate") else ""
                    return finish(base_response(self.yoagent_job_answer(job) + duplicate))
            if not confirmation_required:
                return finish(base_response(self.yoagent_action_answer(action), details=self.yoagent_action_preview_details(action)))
            return finish(base_response(self.yoagent_action_answer(action), actions=[action], details=self.yoagent_action_preview_details(action)))
        settings_payload_data = self.settings_payload()
        activity_for_operator = get_activity_payload() if product_state_needs_activity(question) else {}
        if parse_settings_write(question, settings_payload_data) or parse_settings_read(question, settings_payload_data):
            activity_for_operator = {}
        operator_response = yoagent_operator_response(
            question,
            settings_payload_data,
            activity_for_operator,
            access_role,
            self.save_settings,
        )
        if operator_response:
            operator_response.setdefault("context_lines", yoagent_context_lines(activity_for_operator) if activity_for_operator else [])
            operator_response.setdefault("generated_at", activity_for_operator.get("generated_at") if activity_for_operator else None)
            operator_response.setdefault("session_order", activity_for_operator.get("session_order", []) if activity_for_operator else [])
            return finish(operator_response)
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        answer = ""
        backend_used = "deterministic"
        fallback_reason = ""
        cli_status: dict[str, Any] = {}
        activity_payload = get_activity_payload()
        stream_id = f"chat-{uuid.uuid4().hex}"
        if backend in {"codex", "claude"} and invocation == "cli":
            answer, fallback_reason, cli_status = self.run_yoagent_cli_backend(backend, question, activity_payload, settings, history, locale, stream_id=stream_id)
            if answer:
                backend_used = backend
        elif backend in {"codex", "claude"} and invocation != "cli":
            fallback_reason = f"{backend} {invocation} invocation is not available yet"
        if not answer:
            answer = deterministic_yoagent_reply(question, activity_payload, settings, locale)
        response = base_response(answer, backend=requested_backend, backend_used=backend_used, fallback_reason=fallback_reason, cli=cli_status, include_activity=True)
        if cli_status:
            response["stream_id"] = stream_id
        return finish(response)

    def run_yoagent_cli_backend(
        self,
        backend: str,
        question: str,
        activity_payload: dict[str, Any],
        settings: dict[str, Any],
        history: list[dict[str, str]],
        locale: str = "en",
        stream_id: str = "",
    ) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}

        with self.yoagent_cli_lock:
            state = self.yoagent_cli_sessions.get(backend, {})
            session_id = str(state.get("session_id") or "")
            context_signature = yoagent_activity_payload_signature(activity_payload)
            context_changed = context_signature != state.get("activity_signature")
            seed = not session_id
            next_session_id = session_id or (str(uuid.uuid4()) if backend == "claude" else "")
            prompt = build_yoagent_chat_prompt(question, activity_payload, settings, history, locale) if seed else build_yoagent_resume_prompt(question, activity_payload, settings, context_changed, locale)
            prompt += yoagent_language_directive(locale)

        started = time.monotonic()
        if stream_id:
            self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="started")
        if backend == "codex":
            stream_callback = self.yoagent_stream_callback(stream_id, backend) if stream_id else None
            if stream_callback:
                answer, error, captured_session_id, backend_status = self.run_yoagent_codex_app_server(
                    prompt,
                    session_id=session_id,
                    resume=not seed,
                    settings=settings,
                    stream_callback=stream_callback,
                )
            else:
                answer, error, captured_session_id, backend_status = self.run_yoagent_codex_app_server(prompt, session_id=session_id, resume=not seed, settings=settings)
            next_session_id = captured_session_id or session_id
            if error and not answer:
                fallback_answer, fallback_error, fallback_session_id = self.run_yoagent_codex_cli(prompt, session_id=session_id, resume=not seed, settings=settings)
                backend_status["fast_backend_error"] = error
                backend_status["fallback_transport"] = "codex-exec"
                if fallback_answer:
                    answer = fallback_answer
                    error = ""
                    next_session_id = fallback_session_id or next_session_id
                    backend_status["transport"] = "codex-exec"
                    backend_status["persistent"] = False
                else:
                    error = fallback_error or error
        else:
            claude_model = str(settings.get("claude_model") or YOAGENT_CLAUDE_SUMMARY_MODEL).strip()
            claude_effort = str(settings.get("claude_effort") or "").strip()
            answer, error = self.run_yoagent_claude_cli(prompt, session_id=next_session_id, resume=not seed, model=claude_model, effort=claude_effort)
            backend_status = {"transport": "claude-cli", "persistent": False, "model": claude_model, "effort": claude_effort or None}
        elapsed_ms = round((time.monotonic() - started) * 1000)
        fallback_reason = yoagent_cli_fallback_reason(backend, error)
        status = {
            **backend_status,
            "backend": backend,
            "resumed": not seed,
            "seeded": seed,
            "context_changed": context_changed,
            "prompt_chars": len(prompt),
            "elapsed_ms": elapsed_ms,
            "session_id": next_session_id or None,
            "per_server": True,
        }
        with self.yoagent_cli_lock:
            if answer and next_session_id:
                self.yoagent_cli_sessions[backend] = {
                    "session_id": next_session_id,
                    "activity_signature": context_signature,
                    "updated_ts": time.time(),
                    "updated_monotonic": time.monotonic(),
                }
                yoagent_conversation.save_cli_sessions(self.yoagent_cli_sessions)
            elif fallback_reason:
                self.yoagent_cli_sessions.pop(backend, None)
                yoagent_conversation.save_cli_sessions(self.yoagent_cli_sessions)
        if stream_id:
            visible_answer, hidden_thinking_removed = strip_yoagent_hidden_thinking(answer)
            self.publish_yoagent_stream_delta(
                stream_id,
                visible_answer,
                backend=backend,
                phase="done",
                done=True,
                hidden_thinking_removed=hidden_thinking_removed,
            )
        return answer, fallback_reason, status

    def run_yoagent_codex_cli(self, prompt: str, session_id: str = "", resume: bool = False, settings: dict[str, Any] | None = None) -> tuple[str, str, str]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", ""
        current_settings = settings or self.yoagent_settings()
        args = codex_exec_argv(
            resume_session_id=session_id if resume and session_id else None,
            model=str(current_settings.get("codex_model") or "").strip() or None,
            effort=str(current_settings.get("codex_effort") or "").strip() or None,
            service_tier=SUMMARY_CODEX_SERVICE_TIER,
        )
        try:
                completed = subprocess.run(
                    args,
                    input=prompt,
                    cwd=str(PROJECT_ROOT),
                    env=codex_runtime_env(),
                    text=True,
                    capture_output=True,
                    timeout=YOAGENT_CLI_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", str(exc), ""
        text_parts = []
        captured_session_id = ""
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            captured_session_id = captured_session_id or codex_event_session_id(event)
            text = codex_event_text(event)
            if text:
                text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts).strip(), "", captured_session_id
        error = completed.stderr.strip() or f"codex exited {completed.returncode}"
        return "", error, captured_session_id

    def run_yoagent_claude_cli(self, prompt: str, session_id: str = "", resume: bool = False, model: str = "", effort: str = "") -> tuple[str, str]:
        if not shutil.which("claude"):
            return "", "claude CLI not found"
        args = ["claude", "-p"]
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
        if resume and session_id:
            args.extend(["--resume", session_id])
        elif session_id:
            args.extend(["--session-id", session_id])
        args.append(prompt)
        try:
            completed = subprocess.run(
                args,
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"},
                text=True,
                capture_output=True,
                timeout=YOAGENT_CLI_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", str(exc)
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip(), ""
        return "", completed.stderr.strip() or f"claude exited {completed.returncode}"

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        payload = save_settings(patch)
        self.publish_client_event("settings_changed", {"mtime_ns": payload.get("mtime_ns", 0), "data": payload}, trigger="manual", cache="ready")
        self.client_watch_wake_event.set()
        if self.yoagent_refresh_interval_seconds(payload.get("settings", {}).get("yoagent", {})) > 0:
            self.maybe_start_yoagent_summary_worker()
        return payload

    def yolo_rules_payload(self) -> dict[str, Any]:
        return yolo_rules.rules_status()

    def reload_yolo_rules(self) -> dict[str, Any]:
        return yolo_rules.reload_rules()

    def ensure_yolo_rules_file(self) -> dict[str, Any]:
        yolo_rules.ensure_rule_file()
        return yolo_rules.reload_rules()

    def auto_approve_interval_seconds(self) -> float:
        value = settings_payload().get("settings", {}).get("performance", {}).get("auto_approve_interval_seconds", 0.5)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.5

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

    def search_payload(self, query: str, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        text = str(query or "").strip()
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        event_matches = self.event_log.search(text, session=session, limit=bounded_limit)
        summary_matches: list[dict[str, Any]] = []
        if text:
            search_sessions = [session] if session else self.sessions
            needle = text.lower()
            for name in search_sessions:
                summary, status = self.summary(name)
                summary_text = summary.get("text") if status == HTTPStatus.OK else ""
                if isinstance(summary_text, str) and needle in summary_text.lower():
                    summary_matches.append({
                        "session": name,
                        "type": "summary",
                        "text": truncate_text(summary_text, 2000),
                    })
                    if len(summary_matches) >= bounded_limit:
                        break
        return {
            "query": text,
            "session": session or "",
            "limit": bounded_limit,
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

    def build_activity_payload(self) -> dict[str, Any]:
        sessions, errors = discover_sessions(self.sessions)
        ordered_sessions = self.tmux_recency_ordered_sessions(self.sessions)
        agent_infos = {session: sessions[session] for session in ordered_sessions if session in sessions and sessions[session].agents}
        session_files_by_session = self.cached_session_files_payloads_for_infos(agent_infos, hours=24.0)
        return {
            "activity": self.activity_ledger.snapshot(),
            "agents": build_recent_agents_payload(sessions, ordered_sessions, session_files_by_session=session_files_by_session),
            "errors": errors,
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
                    self.publish_activity_summary_ready_events(trigger="tabber_activity")
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

    def run_history_payload(self, session: str | None = None) -> tuple[RunHistoryPayload, HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        scope = [session] if session else self.sessions
        infos, errors = discover_sessions(scope)
        runs: list[RunHistoryEntry] = []
        for name in scope:
            info = infos.get(name)
            if info is None:
                continue
            selected = info.selected_pane
            agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
            project = session_project_metadata(info, self.metadata_cache, allow_network=False)
            transcript_mtime = 0.0
            if agent and agent.transcript:
                transcript_mtime = session_files.file_mtime(Path(agent.transcript))
            runs.append({
                "session": name,
                "agent": asdict(agent) if agent else None,
                "cwd": agent.cwd if agent and agent.cwd else selected.current_path if selected else "",
                "tmux_target": selected.target if selected else "",
                "tmux_command": selected.process_label or selected.command if selected else "",
                "transcript_mtime": transcript_mtime,
                "project": project,
                "recent_events": self.event_log.tail(session=name, limit=5),
            })
        runs.sort(key=lambda item: (-float(item.get("transcript_mtime") or 0), item["session"]))
        return {"session": session or "", "runs": runs, "errors": [*refresh_errors, *errors]}, HTTPStatus.OK

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

        saved: list[dict[str, Any]] = []
        upload_template = settings_payload().get("settings", {}).get("uploads", {}).get("filename_template")
        for upload in files:
            safe_name = sanitize_upload_filename(upload.filename)
            path = unique_upload_path(target_dir, safe_name, str(upload_template or ""))
            try:
                path.write_bytes(upload.content)
            except OSError as exc:
                return {
                    "session": session,
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
        if not capture_pane:
            # Roster path: derive working/idle from the LIVE pane via a cheap visible-only capture
            # plus cheap prompt presence from the already-captured text. This avoids the expensive
            # hybrid transcript / bash double-capture fan-out while still lighting roster approval badges.
            try:
                visible_text = tmux_capture_pane(target, visible_only=True)
            except (OSError, subprocess.SubprocessError):
                visible_text = None
            if visible_text is None:
                return hidden_prompt, {"key": "idle", "text": ""}
            return normalized_prompt_state(approval_prompt_state(visible_text)), dict(agent_screen_state(visible_text))
        try:
            visible_text = tmux_capture_pane(target, visible_only=True)
            if visible_text is None:
                prompt = normalized_prompt_state()
                prompt["error"] = "failed to capture pane"
                screen = {"key": "disconnected", "text": "failed to capture pane"}
                return prompt, screen
            prompt_state = hybrid_approval_prompt_state(session, visible_text, prompt_source=self.auto_approve_prompt_source())
            if prompt_state.get("visible") and prompt_state.get("type") == "bash":
                pane_text = tmux_capture_pane(target)
                prompt_state = hybrid_approval_prompt_state(session, visible_text, pane_text or visible_text, prompt_source=self.auto_approve_prompt_source())
            screen_state = agent_screen_state(visible_text)
            if screen_state.get("key") == "idle":
                # Visible pane state is primary. Transcript activity only upgrades idle -> working
                # when recent, matching docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md#transcript-signals.
                infos = discovered_sessions
                if infos is None:
                    infos, _errors = discover_sessions([session])
                transcript_state = session_transcript_activity_state(infos.get(session))
                if transcript_state.get("key") != "idle":
                    screen_state = transcript_state
            return normalized_prompt_state(prompt_state), dict(screen_state)
        except (OSError, subprocess.SubprocessError) as exc:
            prompt = normalized_prompt_state()
            prompt["error"] = str(exc)
            screen = {"key": "error", "text": str(exc)}
            return prompt, screen

    def auto_approve_session_status(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        include_live_prompt: bool = True,
        capture_bare_session_when_roster: bool = False,
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
        prompt, screen = self.prompt_and_screen_status(
            session,
            discovered_sessions=discovered_sessions,
            capture_pane=include_live_prompt,
            capture_bare_session_when_roster=capture_bare_session_when_roster,
        )
        payload["prompt"] = prompt
        payload["screen"] = screen
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
        if session is not None:
            return self.auto_approve_session_status(session), HTTPStatus.OK
        discovered_sessions, discovery_errors = discover_sessions(self.sessions)
        return {
            "session_order": self.sessions,
            "sessions": {
                name: self.auto_approve_session_status(
                    name,
                    discovered_sessions=discovered_sessions,
                    include_live_prompt=False,
                    capture_bare_session_when_roster=True,
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

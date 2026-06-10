from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

import auto_approve_tmux

from . import filesystem
from . import session_files
from . import yolo_rules
from .activity_summary import activity_signature
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
from .client_events import ClientEventBroker
from .common import AGENT_COMMANDS
from .common import EVENT_LOG_PATH
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_EVENT_TAIL_LINES
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import MAX_YOLOMUX_SESSION_TABS
from .common import PROJECT_ROOT
from .common import SERVER_HOSTNAME
from .common import SUMMARY_MAX_PROMPT_CHARS
from .common import YOLOMUX_VERSION
from .common import YOAGENT_CLAUDE_SUMMARY_MODEL
from .common import UPLOAD_MAX_FILES
from .common import UPLOAD_MAX_BYTES
from .common import as_dict
from .common import codex_exec_argv
from .common import next_numbered_session_name
from .common import tail_file_lines
from .common import truncate_text
from .control import YolomuxControlServer
from .control import send_yolomux_control_request
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
from .transcripts import session_transcript_activity_state
from .transcripts import trim_prompt_text
from .tmux_utils import list_tmux_session_names
from .tmux_utils import tmux
from .tmux_utils import tmux_has_exact_session
from .tmux_utils import tmux_session_target
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


METADATA_BADGE_PULSE_SECONDS = 20.0
METADATA_BADGES = ("main", "pr", "status", "ci")
METADATA_BADGE_SIGNATURES_STATE_KEY = "metadata_badge_signatures"
METADATA_BADGE_PULSE_UNTIL_STATE_KEY = "metadata_badge_pulse_until"
YOAGENT_CLI_TIMEOUT_SECONDS = 45
YOAGENT_CLI_SESSION_IDLE_SECONDS = 300
YOAGENT_SESSION_SUMMARIES_STATE_KEY = "yoagent_session_summaries"
YOAGENT_SESSION_SUMMARY_STATES = {"working", "waiting", "blocked", "done", "idle"}
YOAGENT_SESSION_SUMMARY_QUIET_SECONDS = 15.0
YOAGENT_SESSION_SUMMARY_MAX_ITEMS = 120
YOAGENT_AUTH_FAILURE_RE = re.compile(
    r"(not\s+logged\s+in|log\s*in|login|required\s+auth|authentication|unauthorized|permission\s+denied|401)",
    re.IGNORECASE,
)
SESSION_FILES_CACHE_MAX_ITEMS = 64
SESSION_FILES_CACHE_SECONDS = 5.0
TRANSCRIPT_TAIL_CACHE_MAX_ITEMS = 128
TRANSCRIPTS_PAYLOAD_CACHE_SECONDS = 15.0
CONTEXT_ITEMS_CACHE_MAX_ITEMS = 128
SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS = 1.25
SERVER_WATCHED_PR_EVENT_POLL_SECONDS = 60.0
CLIENT_WATCH_ROOT_TTL_SECONDS = 300
CLIENT_WATCH_ROOT_LIMIT = 128
CLIENT_WATCH_FILE_LIMIT = 128
DIRECTORY_WATCH_ENTRY_LIMIT = 512
# Keep in sync with tmuxSessionNameError() in static/yolomux.js.
TMUX_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,64}$")


def yoagent_cli_auth_failure(text: str) -> bool:
    return bool(YOAGENT_AUTH_FAILURE_RE.search(text or ""))


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
    # Use the canonical login command (DOIT.6 #39 verified `claude auth login`, not `claude login`).
    login_command = AGENT_LOGIN_COMMANDS.get(backend, f"{backend} login")
    return f"{label} is not logged in. Run `{login_command}`; showing the No agent YO!agent summary."


def yoagent_language_directive(locale: str) -> str:
    locale_id = str(locale or "").strip()
    if locale_id in {"", "en", "en-XA", "system"}:
        return ""
    directive = server_string(locale_id, "yoagent.prompt.answerLanguage").strip()
    return f"\n\n{directive}" if directive else ""


def resolve_yoagent_backend(backend: str) -> str:
    # DOIT.6 #41: the default backend is "auto" — prefer codex, then claude, falling back to the
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
    state = auto_approve_tmux.blank_prompt_state()
    if prompt:
        state.update(prompt)
    return state


class TmuxWebtermApp:
    def __init__(self, sessions: list[str], dangerously_yolo: bool = False):
        self.sessions = sessions
        self.dangerously_yolo = dangerously_yolo
        self.auto_workers: dict[str, AutoApproveWorker] = {}
        self.auto_workers_lock = threading.RLock()
        self.metadata_cache = MetadataCache()
        # DOIT.34 #4: last-logged watched-PR truncation state, so the cap is logged only when it changes.
        self._watched_pr_truncated_signature: tuple[int, tuple[str, ...]] | None = None
        self.metadata_warm_lock = threading.Lock()
        self.metadata_warm_running = False
        self.metadata_badge_lock = threading.Lock()
        self.metadata_badge_signatures: dict[str, dict[str, str]] = {}
        self.metadata_badge_pulse_until: dict[str, dict[str, float]] = {}
        self.client_events = ClientEventBroker()
        self.client_watch_lock = threading.RLock()
        self.client_watch_roots: dict[str, float] = {}
        self.client_watch_files: dict[str, float] = {}
        self.client_watch_context_items: list[dict[str, Any]] = []
        self.client_watch_session_files: list[dict[str, Any]] = []
        self.client_watch_activity_summary: dict[str, Any] = {}
        self.client_watch_initialized = False
        self.client_watch_settings_signature: tuple[Any, ...] | None = None
        self.client_watch_transcripts_signature: tuple[Any, ...] | None = None
        self.client_watch_filesystem_signature: tuple[Any, ...] | None = None
        self.client_watch_file_signature: tuple[Any, ...] | None = None
        self.client_watch_auto_approve_signature: str = ""
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
        self.client_event_next_auto_poll_at = 0.0
        self.client_event_next_watched_pr_poll_at = 0.0
        self.activity_summary_lock = threading.RLock()
        self.activity_summary_cache: dict[str, dict[str, Any]] = {}
        self.session_files_cache_lock = threading.RLock()
        self.session_files_cache: dict[tuple[Any, ...], tuple[float, tuple[dict[str, Any], HTTPStatus]]] = {}
        self.session_files_refreshing_cache_keys: set[tuple[Any, ...]] = set()
        self.transcripts_payload_cache_lock = threading.RLock()
        self.transcripts_payload_cache: tuple[float, dict[str, Any]] | None = None
        self.transcripts_payload_refreshing = False
        self.transcript_tail_cache_lock = threading.RLock()
        self.transcript_tail_cache: dict[tuple[Any, ...], tuple[float, str]] = {}
        self.context_items_cache_lock = threading.RLock()
        self.context_items_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
        self.yoagent_cli_lock = threading.RLock()
        self.yoagent_cli_sessions: dict[str, dict[str, Any]] = {}
        self.yoagent_prewarm_lock = threading.Lock()
        self.yoagent_prewarm_running = False
        self.yoagent_prewarm_status: dict[str, Any] = {}
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

    def refresh_sessions(self) -> list[str]:
        sessions, error = list_tmux_session_names()
        if error is None:
            self.sessions = sessions
            self.prune_yoagent_session_summaries(set(sessions))
            return []
        return [error]

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
            local_enabled = {name for name, worker in self.auto_workers.items() if worker.alive()}
        current = read_yolomux_state().get("auto_approve_enabled", [])
        if isinstance(current, list):
            external_enabled = {
                session
                for session in current
                if isinstance(session, str) and session not in self.auto_workers and auto_approve_lock_owner(session)
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

    def server_auto_approve_event_poll_seconds(self) -> float:
        return SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS

    def server_watched_pr_event_poll_seconds(self) -> float:
        return SERVER_WATCHED_PR_EVENT_POLL_SECONDS

    def client_event_watch_sleep_seconds(self, now: float) -> float:
        next_due = min(
            self.client_event_next_signature_poll_at,
            self.client_event_next_file_poll_at,
            self.client_event_next_auto_poll_at,
            self.client_event_next_watched_pr_poll_at,
        )
        if next_due <= 0:
            return self.server_event_poll_seconds()
        return max(0.01, min(60.0, next_due - now))

    def update_client_watch_roots(self, roots: Any) -> dict[str, Any]:
        now = time.monotonic()
        payload = roots if isinstance(roots, dict) else {"roots": roots}
        normalized: list[str] = []
        raw_roots = payload.get("roots", []) if isinstance(payload, dict) else []
        if isinstance(raw_roots, list):
            for item in raw_roots:
                path = str(item or "").strip()
                if not path.startswith("/"):
                    continue
                normalized.append(str(Path(path).expanduser()))
        unique = sorted(set(normalized))[:CLIENT_WATCH_ROOT_LIMIT]
        normalized_files: list[str] = []
        raw_files = payload.get("files", []) if isinstance(payload, dict) else []
        if isinstance(raw_files, list):
            for item in raw_files:
                path = str(item or "").strip()
                if not path.startswith("/"):
                    continue
                normalized_files.append(str(Path(path).expanduser()))
        unique_files = sorted(set(normalized_files))[:CLIENT_WATCH_FILE_LIMIT]
        context_items = self.normalized_client_context_items(payload.get("context_items", []))
        session_files_requests = self.normalized_client_session_files(payload.get("session_files", []))
        activity_summary = self.normalized_client_activity_summary(payload.get("activity_summary", {}))
        with self.client_watch_lock:
            self.client_watch_roots = {path: now + CLIENT_WATCH_ROOT_TTL_SECONDS for path in unique}
            self.client_watch_files = {path: now + CLIENT_WATCH_ROOT_TTL_SECONDS for path in unique_files}
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
        now = time.monotonic()
        with self.client_watch_lock:
            current = {path: expires for path, expires in self.client_watch_roots.items() if expires > now}
            if len(current) != len(self.client_watch_roots):
                self.client_watch_roots = current
            return sorted(current)

    def client_watch_files_snapshot(self) -> list[str]:
        now = time.monotonic()
        with self.client_watch_lock:
            current = {path: expires for path, expires in self.client_watch_files.items() if expires > now}
            if len(current) != len(self.client_watch_files):
                self.client_watch_files = current
            return sorted(current)

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
        roots = set(self.client_watch_roots_snapshot())
        settings = settings_payload().get("settings", {})
        file_explorer = settings.get("file_explorer", {}) if isinstance(settings, dict) else {}
        if isinstance(file_explorer, dict):
            for item in file_explorer.get("companion_dirs", []) or []:
                path = str(item or "").strip()
                if path.startswith("/"):
                    roots.add(path)
        return sorted(str(Path(root).expanduser()) for root in roots if str(root or "").startswith("/"))[:CLIENT_WATCH_ROOT_LIMIT]

    def filesystem_roots_watch_signature(self, sessions: dict[str, SessionInfo]) -> tuple[Any, ...]:
        return tuple((root, filesystem_watch_signature(root)) for root in self.filesystem_roots_for_watch(sessions))

    def files_for_watch(self) -> list[str]:
        return self.client_watch_files_snapshot()[:CLIENT_WATCH_FILE_LIMIT]

    def files_watch_signature(self) -> tuple[Any, ...]:
        return tuple((path, file_watch_signature(path)) for path in self.files_for_watch())

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
                if now >= self.client_event_next_signature_poll_at:
                    self.client_event_next_signature_poll_at = now + self.server_directory_event_poll_seconds()
                    self.start_client_directory_poll()
                if now >= self.client_event_next_auto_poll_at:
                    self.poll_auto_approve_client_event_once()
                    self.client_event_next_auto_poll_at = now + self.server_auto_approve_event_poll_seconds()
                if now >= self.client_event_next_watched_pr_poll_at:
                    self.poll_watched_prs_client_event_once()
                    self.client_event_next_watched_pr_poll_at = now + self.server_watched_pr_event_poll_seconds()
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

    def get_session_files_cache(
        self,
        key: tuple[Any, ...],
        max_age_seconds: float | None = None,
        allow_stale: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus, bool, float] | None:
        now = time.monotonic()
        with self.session_files_cache_lock:
            cached = self.session_files_cache.get(key)
            if not cached:
                return None
            stored_at, value = cached
            age_seconds = max(0.0, now - stored_at)
            fresh = max_age_seconds is None or age_seconds <= max_age_seconds
            if not fresh and not allow_stale:
                return None
            payload, status = value
            return copy.deepcopy(payload), status, fresh, age_seconds

    def set_session_files_cache(self, key: tuple[Any, ...], payload: dict[str, Any], status: HTTPStatus) -> None:
        with self.session_files_cache_lock:
            self.cache_set_limited(
                self.session_files_cache,
                key,
                (time.monotonic(), (copy.deepcopy(payload), status)),
                SESSION_FILES_CACHE_MAX_ITEMS,
            )

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
            payload, status = session_files.session_files_payload(session, infos, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
            self.set_session_files_cache(cache_key, payload, status)
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
            payload = session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
            self.set_session_files_cache(cache_key, payload, HTTPStatus.OK)
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
        key = self.session_files_cache_key("info", infos, info.session, hours, from_ref, to_ref, repo_refs)
        cached = self.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=True)
        if cached:
            payload, _status, fresh, _age = cached
            if not fresh:
                self.start_session_files_cache_refresh(key, self.refresh_session_files_info_cache, info, hours, from_ref, to_ref, repo_refs)
            return payload
        payload = session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
        self.set_session_files_cache(key, payload, HTTPStatus.OK)
        return copy.deepcopy(payload)

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
        # DOIT.29: resolve the github.watched_prs watchlist to live PR metadata, independent of any open
        # session's branch. The server-side SSE loop refreshes it on a fixed slow cadence so a big watchlist
        # does not exhaust the GitHub rate limit.
        settings = settings_payload().get("settings", {})
        refs = settings.get("github", {}).get("watched_prs", [])
        result = watched_pr_metadata(refs, self.metadata_cache, allow_network=allow_network)
        # DOIT.34 #4: log the truncation only when the capped state CHANGES (count or watchlist), not on
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

    def activity_summary_payload(self, force: bool = False, locale: str = "en") -> dict[str, Any]:
        locale = str(locale or "en").strip() or "en"
        sessions, errors = discover_sessions(self.sessions)
        self.warm_metadata_cache_async(sessions)
        self.prune_yoagent_session_summaries(set(sessions))
        summaries: dict[str, Any] = {}
        ordered_summaries: list[dict[str, Any]] = []
        with self.activity_summary_lock:
            if force:
                self.activity_summary_cache.clear()
                self.clear_session_files_cache()
            for session in self.sessions:
                info = sessions.get(session)
                if info is None:
                    continue
                project = session_project_metadata(info, self.metadata_cache, allow_network=False)
                files_payload = self.cached_session_files_payload_for_info(info, hours=24.0)
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
            "session_order": [session for session in self.sessions if session in summaries],
            "sessions": summaries,
            "global": build_global_activity_summary(ordered_summaries, errors),
            "capabilities": yoagent_capabilities_payload(),
            "errors": errors,
            "locale": locale,
            "yoagent_summaries": {
                "auto_refresh": bool(settings.get("auto_refresh", False)),
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
        value = (settings or self.yoagent_settings()).get("refresh_interval_seconds", 120)
        return max(30.0, min(3600.0, self.float_value(value, 120.0)))

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

    def run_yoagent_direct_prompt_backend(self, backend: str, prompt: str) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}
        started = time.monotonic()
        if backend == "codex":
            answer, error, _session_id = self.run_yoagent_codex_cli(prompt, session_id="", resume=False)
        else:
            answer, error = self.run_yoagent_claude_cli(prompt, session_id="", resume=False, model=YOAGENT_CLAUDE_SUMMARY_MODEL)
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
        answer, fallback_reason, cli_status = self.run_yoagent_direct_prompt_backend(backend, prompt)
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
        if not current_settings.get("auto_refresh", False):
            return {"enabled": False, "updated": [], "skipped": []}
        interval = self.yoagent_refresh_interval_seconds(current_settings)
        sessions, errors = discover_sessions(self.sessions)
        self.prune_yoagent_session_summaries(set(sessions))
        updated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        now = time.time()
        for session in self.sessions:
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
        if not settings.get("auto_refresh", False):
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
                if not settings.get("auto_refresh", False):
                    return
                self.tick_yoagent_session_summaries(settings)
                time.sleep(min(self.yoagent_refresh_interval_seconds(settings), 60.0))
        finally:
            with self.yoagent_summary_worker_lock:
                self.yoagent_summary_worker_running = False

    def yoagent_settings(self) -> dict[str, Any]:
        settings = settings_payload().get("settings", {}).get("yoagent", {})
        return settings if isinstance(settings, dict) else {}

    def reset_yoagent_chat(self) -> dict[str, Any]:
        with self.yoagent_cli_lock:
            self.yoagent_cli_sessions.clear()
        with self.yoagent_prewarm_lock:
            self.yoagent_prewarm_status = {}
        return {"ok": True}

    def yoagent_prewarm(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        settings = self.yoagent_settings()
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        if backend not in {"codex", "claude"} or invocation != "cli":
            return {"ok": True, "started": False, "backend": requested_backend, "backend_used": backend, "reason": "no CLI backend available"}, HTTPStatus.OK
        with self.yoagent_prewarm_lock:
            if self.yoagent_prewarm_running:
                return {"ok": True, "started": False, "backend": requested_backend, "backend_used": backend, "reason": "already running"}, HTTPStatus.ACCEPTED
            self.yoagent_prewarm_running = True
            self.yoagent_prewarm_status = {"backend": backend, "started_at": time.time()}
        locale = str((payload or {}).get("locale") or "en").strip() or "en"

        def worker() -> None:
            status: dict[str, Any] = {"backend": backend}
            try:
                activity_payload = self.activity_summary_payload()
                answer, reason, cli_status = self.run_yoagent_cli_backend(
                    backend,
                    server_string(locale, "yoagent.prompt.prewarmQuestion"),
                    activity_payload,
                    settings,
                    [],
                    locale,
                )
                status = {"backend": backend, "warmed": bool(answer and not reason), "fallback_reason": reason, "cli": cli_status}
            except Exception as exc:
                status = {"backend": backend, "warmed": False, "error": str(exc)}
            finally:
                with self.yoagent_prewarm_lock:
                    self.yoagent_prewarm_status = status
                    self.yoagent_prewarm_running = False

        threading.Thread(target=worker, name="yoagent-prewarm", daemon=True).start()
        return {"ok": True, "started": True, "backend": requested_backend, "backend_used": backend}, HTTPStatus.ACCEPTED

    def yoagent_chat(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        question = truncate_text(" ".join(str(payload.get("message") or payload.get("question") or "").split()), 4000)
        if not question:
            return {"error": "missing YO!agent message"}, HTTPStatus.BAD_REQUEST
        raw_history = payload.get("history", [])
        history = []
        if isinstance(raw_history, list):
            for item in raw_history[-8:]:
                if not isinstance(item, dict):
                    continue
                role = "user" if item.get("role") == "user" else "assistant"
                content = truncate_text(" ".join(str(item.get("content") or "").split()), 1000)
                if content:
                    history.append({"role": role, "content": content})
        settings = self.yoagent_settings()
        locale = str(payload.get("locale") or "en").strip()
        activity_payload = self.activity_summary_payload()
        context_lines = yoagent_context_lines(activity_payload)
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        answer = ""
        backend_used = "deterministic"
        fallback_reason = ""
        cli_status: dict[str, Any] = {}
        if backend in {"codex", "claude"} and invocation == "cli":
            answer, fallback_reason, cli_status = self.run_yoagent_cli_backend(backend, question, activity_payload, settings, history, locale)
            if answer:
                backend_used = backend
        elif backend in {"codex", "claude"} and invocation != "cli":
            fallback_reason = f"{backend} {invocation} invocation is not available yet"
        if not answer:
            answer = deterministic_yoagent_reply(question, activity_payload, settings, locale)
        return {
            "answer": answer,
            "answered_at": datetime.now(timezone.utc).isoformat(),
            "backend": requested_backend,
            "backend_used": backend_used,
            "fallback": bool(fallback_reason),
            "fallback_reason": fallback_reason,
            "cli": cli_status,
            "context_lines": context_lines,
            "generated_at": activity_payload.get("generated_at"),
            "session_order": activity_payload.get("session_order", []),
        }, HTTPStatus.OK

    def run_yoagent_cli_backend(
        self,
        backend: str,
        question: str,
        activity_payload: dict[str, Any],
        settings: dict[str, Any],
        history: list[dict[str, str]],
        locale: str = "en",
    ) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}

        with self.yoagent_cli_lock:
            now = time.monotonic()
            state = self.yoagent_cli_sessions.get(backend, {})
            if state and now - float(state.get("updated_monotonic") or 0) > YOAGENT_CLI_SESSION_IDLE_SECONDS:
                state = {}
                self.yoagent_cli_sessions.pop(backend, None)
            session_id = str(state.get("session_id") or "")
            context_signature = yoagent_activity_payload_signature(activity_payload)
            context_changed = context_signature != state.get("activity_signature")
            seed = not session_id
            next_session_id = session_id or (str(uuid.uuid4()) if backend == "claude" else "")
            prompt = build_yoagent_chat_prompt(question, activity_payload, settings, history, locale) if seed else build_yoagent_resume_prompt(question, activity_payload, settings, context_changed, locale)
            prompt += yoagent_language_directive(locale)

        started = time.monotonic()
        if backend == "codex":
            answer, error, captured_session_id = self.run_yoagent_codex_cli(prompt, session_id=session_id, resume=not seed)
            next_session_id = captured_session_id or session_id
        else:
            answer, error = self.run_yoagent_claude_cli(prompt, session_id=next_session_id, resume=not seed)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        fallback_reason = yoagent_cli_fallback_reason(backend, error)
        status = {
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
                    "updated_monotonic": time.monotonic(),
                }
            elif fallback_reason:
                self.yoagent_cli_sessions.pop(backend, None)
        return answer, fallback_reason, status

    def run_yoagent_codex_cli(self, prompt: str, session_id: str = "", resume: bool = False) -> tuple[str, str, str]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", ""
        args = codex_exec_argv(resume_session_id=session_id if resume and session_id else None)
        try:
            completed = subprocess.run(
                args,
                input=prompt,
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"},
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

    def run_yoagent_claude_cli(self, prompt: str, session_id: str = "", resume: bool = False, model: str = "") -> tuple[str, str]:
        if not shutil.which("claude"):
            return "", "claude CLI not found"
        args = ["claude", "-p"]
        if model:
            args.extend(["--model", model])
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
        if payload.get("settings", {}).get("yoagent", {}).get("auto_refresh", False):
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
        discover_scope = self.sessions if session else scope
        infos, errors = discover_sessions(discover_scope)
        cache_key = self.session_files_cache_key("payload", infos, session, hours, from_ref, to_ref, repo_refs)
        max_age = SESSION_FILES_CACHE_SECONDS
        cached = None if force else self.get_session_files_cache(cache_key, max_age_seconds=max_age, allow_stale=True)
        cache_meta: dict[str, Any] | None = None
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
            payload, status = session_files.session_files_payload(session, infos, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
            self.set_session_files_cache(cache_key, payload, status)
            cache_meta = {
                "hit": False,
                "stale": False,
                "age_seconds": 0,
                "refresh_seconds": max_age,
                "refreshing": False,
            }
        payload["errors"] = [*refresh_errors, *errors, *payload.get("errors", [])]
        payload["cache"] = cache_meta
        return payload, status

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
            worker = self.auto_workers.get(session)
            if worker is None:
                return {"ok": True, "session": session, "enabled": False, "message": "YOLO was not enabled here"}
            # DOIT.6 #70: confirm the worker thread actually exited (and released its flock) BEFORE
            # reporting released — otherwise the requester could re-acquire while this worker is still
            # alive and about to fire one more keystroke (two workers on one session).
            released = worker.stop()
            self.auto_workers.pop(session, None)
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
            "session_order": self.sessions,
            "sessions": session_payloads,
            # DOIT.6 #39: refresh agent login status on the metadata poll (cached server-side) so the
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

    def tmux_snapshot(self, session: str, lines: int) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        target = info.selected_pane.target if info and info.selected_pane else session
        # DOIT.6 #144: -J rejoins tmux-wrapped lines so a wrapped command is captured as one logical line.
        result = tmux(["capture-pane", "-t", target, "-p", "-J", "-S", f"-{max(1, min(lines, 1000))}"], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux capture-pane failed").strip()
            return {"session": session, "target": target, "errors": [*errors, error]}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {
            "session": session,
            "target": target,
            "text": result.stdout.rstrip("\n"),
            "errors": errors,
        }, HTTPStatus.OK

    def transcript_tail(self, session: str, lines: int) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
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

    def summary(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
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

    def tmux_next_window(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        result = tmux(["next-window", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux next-window failed").strip()
            return {"session": session, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {"session": session, "ok": True}, HTTPStatus.OK

    def stop_auto_approve_worker(self, session: str) -> None:
        with self.auto_workers_lock:
            worker = self.auto_workers.pop(session, None)
        if worker is not None:
            worker.stop()
        self.set_persisted_auto_session(session, False)

    def rename_session(self, session: str, new_name: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        new_name = str(new_name or "").strip()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        name_error = tmux_session_name_error(new_name)
        if name_error:
            return {"session": session, "new_name": new_name, "error": name_error}, HTTPStatus.BAD_REQUEST
        if new_name != session and new_name in self.sessions:
            return {"session": session, "new_name": new_name, "error": f"session already exists: {new_name}"}, HTTPStatus.CONFLICT
        if new_name == session:
            return {"session": session, "new_session": new_name, "renamed": False, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

        result = tmux(["rename-session", "-t", tmux_session_target(session), new_name], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux rename-session failed").strip()
            return {"session": session, "new_name": new_name, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.refresh_sessions()
        self.log_event(new_name, "session_renamed", f"renamed {session} to {new_name}", {"old_session": session, "new_session": new_name})
        return {"session": session, "new_session": new_name, "renamed": True, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

    def kill_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

        result = tmux(["kill-session", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux kill-session failed").strip()
            return {"session": session, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
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

    def tmux_copy_selection(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        target = info.selected_pane.target if info and info.selected_pane else tmux_session_target(session)

        mode = tmux(["display-message", "-p", "-t", target, "#{pane_in_mode}"], timeout=1.0)
        if mode.returncode != 0:
            error = (mode.stderr or mode.stdout or "tmux pane mode check failed").strip()
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
            error = (copied.stderr or copied.stdout or "tmux copy selection failed").strip()
            return {"session": session, "target": target, "copied": False, "text": "", "error": error, "errors": errors}, HTTPStatus.OK

        after = tmux(["display-message", "-p", "-t", target, "#{buffer_created}:#{buffer_size}:#{buffer_sample}"], timeout=1.0)
        if after.returncode != 0:
            error = (after.stderr or after.stdout or "tmux buffer check failed").strip()
            return {"session": session, "target": target, "error": error, "errors": errors}, HTTPStatus.INTERNAL_SERVER_ERROR
        if after.stdout.strip() == before_signature:
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
            error = (buffer_result.stderr or buffer_result.stdout or "tmux save buffer failed").strip()
            return {"session": session, "target": target, "error": error, "errors": errors}, HTTPStatus.INTERNAL_SERVER_ERROR

        text = buffer_result.stdout
        return {
            "session": session,
            "target": target,
            "copied": bool(text),
            "text": text,
            "chars": len(text),
            "errors": errors,
        }, HTTPStatus.OK

    def ensure_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

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
            error = (result.stderr or result.stdout or "tmux new-session failed").strip()
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

    def upload_files(self, session: str, files: list[UploadedFile]) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
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

    def upload_max_bytes(self) -> int:
        value = settings_payload().get("settings", {}).get("uploads", {}).get("max_bytes", UPLOAD_MAX_BYTES)
        return int(value) if isinstance(value, (int, float)) and value > 0 else UPLOAD_MAX_BYTES

    def upload_target_dir(self, session: str) -> tuple[Path | None, str]:
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

    def set_auto_approve(self, session: str, enabled: bool, persist: bool = True, takeover: bool = True) -> tuple[AutoApproveState, HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

        with self.auto_workers_lock:
            existing = self.auto_workers.get(session)
            if existing and not existing.alive():
                self.auto_workers.pop(session, None)
                existing = None
                if persist:
                    self.persist_auto_sessions()

            if enabled:
                if existing:
                    return self.auto_approve_session_status(session), HTTPStatus.OK
                if not tmux_has_exact_session(session):
                    return {"session": session, "enabled": False, "error": f"tmux session not found: {session}"}, HTTPStatus.NOT_FOUND
                worker, status = self.start_auto_approve_worker(session, takeover=takeover)
                if worker is None:
                    return status, HTTPStatus.CONFLICT
                self.auto_workers[session] = worker
                if persist:
                    self.set_persisted_auto_session(session, True)
                self.log_event(session, "yolo_enabled", "YOLO enabled", {"persist": persist})
                return self.auto_approve_session_status(session), HTTPStatus.OK

            if existing:
                existing.stop()
                self.auto_workers.pop(session, None)
                if persist:
                    self.set_persisted_auto_session(session, False)
                self.log_event(session, "yolo_disabled", "YOLO disabled", {"persist": persist})
        return self.auto_approve_session_status(session), HTTPStatus.OK

    def start_auto_approve_worker(self, session: str, takeover: bool) -> tuple[AutoApproveWorker | None, AutoApproveState]:
        worker = AutoApproveWorker(
            session,
            interval=self.auto_approve_interval_seconds(),
            event_callback=self.log_auto_event,
            owner_extra=self.control_server.owner_payload(),
            dangerously_yolo=self.dangerously_yolo,
            prompt_source=self.auto_approve_prompt_source(),
        )
        started, owner = worker.start()
        if started:
            return worker, worker.status()
        locked_owner = owner
        if takeover and self.request_auto_approve_release(session, owner):
            # #69: re-acquire with the SINGLE atomic non-blocking flock (worker.start), retried briefly to
            # absorb any lag between the owner's ok and its flock release. Each attempt is atomic, so a
            # third instance grabbing the lock in the gap simply fails the acquire (reported locked) —
            # never a double-owner.
            deadline = time.monotonic() + 2.0
            while True:
                worker = AutoApproveWorker(
                    session,
                    interval=self.auto_approve_interval_seconds(),
                    event_callback=self.log_auto_event,
                    owner_extra=self.control_server.owner_payload(),
                    dangerously_yolo=self.dangerously_yolo,
                    prompt_source=self.auto_approve_prompt_source(),
                )
                started, owner = worker.start()
                if started:
                    self.log_event(session, "yolo_takeover", "YOLO moved from another server", {"owner": locked_owner or {}})
                    return worker, worker.status()
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        payload: AutoApproveState = worker.status()
        payload.update({
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
        # DOIT.6 #69: the owner stopped its worker and released the flock before replying ok (it joins the
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
            agent = next((item for item in info.agents if item.pane_target), None)
            if agent is not None:
                return agent.pane_target
        return session

    def prompt_and_screen_status(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        capture_pane: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        hidden_prompt = normalized_prompt_state()
        target = self.auto_approve_capture_target(session, discovered_sessions=discovered_sessions)
        if not capture_pane:
            # Roster path: derive working/idle from the LIVE pane via a cheap visible-only capture
            # plus cheap prompt presence from the already-captured text. This avoids the expensive
            # hybrid transcript / bash double-capture fan-out while still lighting roster approval badges.
            module = auto_approve_tmux
            try:
                visible_text = module.tmux_capture_pane(target, visible_only=True)
            except (OSError, subprocess.SubprocessError):
                visible_text = None
            if visible_text is None:
                return hidden_prompt, {"key": "idle", "text": ""}
            return normalized_prompt_state(module.approval_prompt_state(visible_text)), dict(module.agent_screen_state(visible_text))
        try:
            module = auto_approve_tmux
            visible_text = module.tmux_capture_pane(target, visible_only=True)
            if visible_text is None:
                prompt = normalized_prompt_state()
                prompt["error"] = "failed to capture pane"
                screen = {"key": "disconnected", "text": "failed to capture pane"}
                return prompt, screen
            prompt_state = module.hybrid_approval_prompt_state(session, visible_text, prompt_source=self.auto_approve_prompt_source())
            if prompt_state.get("visible") and prompt_state.get("type") == "bash":
                pane_text = module.tmux_capture_pane(target)
                prompt_state = module.hybrid_approval_prompt_state(session, visible_text, pane_text or visible_text, prompt_source=self.auto_approve_prompt_source())
            screen_state = module.agent_screen_state(visible_text)
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
            screen = {"key": "error", "text": str(exc)}
            return prompt, screen

    def auto_approve_session_status(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        include_live_prompt: bool = True,
    ) -> AutoApproveState:
        with self.auto_workers_lock:
            worker = self.auto_workers.get(session)
        if worker:
            payload: AutoApproveState = worker.status()
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
            owner = auto_approve_lock_owner(session)
            if owner:
                payload.update({
                    "enabled_elsewhere": True,
                    "locked": True,
                    "lock_owner": owner,
                    "last_action": auto_approve_lock_message(owner),
                    "error": auto_approve_lock_message(owner),
                })
        prompt, screen = self.prompt_and_screen_status(session, discovered_sessions=discovered_sessions, capture_pane=include_live_prompt)
        payload["prompt"] = prompt
        payload["screen"] = screen
        return payload

    def auto_approve_status(self, session: str | None = None) -> tuple[AutoApproveState | AutoApproveStatusPayload, HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        if session is not None and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        removed = False
        with self.auto_workers_lock:
            for name, worker in list(self.auto_workers.items()):
                if not worker.alive():
                    self.log_event(name, "worker_stopped", "YOLO worker stopped", worker.status())
                    self.auto_workers.pop(name, None)
                    removed = True
        if removed:
            self.persist_auto_sessions()
        if session is not None:
            return self.auto_approve_session_status(session), HTTPStatus.OK
        discovered_sessions, discovery_errors = discover_sessions(self.sessions)
        return {
            "session_order": self.sessions,
            "sessions": {
                name: self.auto_approve_session_status(name, discovered_sessions=discovered_sessions, include_live_prompt=False)
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
        self.control_server.stop()

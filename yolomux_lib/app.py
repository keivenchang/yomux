from __future__ import annotations

import collections
import copy
import hashlib
import hmac
import json
import logging
import math
import os
import random
import re
import resource
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
from . import file_index
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
from .background_owner import BACKGROUND_ROLE_SEARCH_INDEX
from .background_owner import BACKGROUND_ROLE_SESSION_FILES
from .background_owner import BACKGROUND_ROLE_STATS_SAMPLER
from .background_owner import BACKGROUND_ROLE_TABBER_ACTIVITY
from .background_owner import BACKGROUND_ROLE_WATCH_ROOTS
from .background_owner import BackgroundOwnerRegistry
from .background_owner import DisabledBackgroundOwner
from .atomic_file import atomic_write_text
from .atomic_file import file_lock
from .cache import MISS as CACHE_MISS
from .cache import TtlCache
from .client_events import CLIENT_EVENT_TYPES
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
from .common import SessionInfo
from .common import SUMMARY_MAX_PROMPT_CHARS
from .common import WATCH_INDEX_PATH
from .common import YOLOMUX_VERSION
from .common import UPLOAD_MAX_FILES
from .common import UPLOAD_MAX_BYTES
from .common import as_dict
from .common import next_numbered_session_name
from .common import project_git
from .common import tail_file_lines
from .common import truncate_text
from .common import yolomux_client_revision
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
from .metadata import git_inventory
from .metadata import indexed_repo_summaries
from .metadata import metadata_build_cache
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
from .transcripts import terminal_input_counts_as_user_activity
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
from .tmux_utils import tmux_session_client_rows
from .tmux_utils import tmux_session_target
from .tmux_theme import apply_tmux_theme_color_to_existing
from .tmux_theme import apply_tmux_theme_color_to_new_session
from .tmux_theme import tmux_theme_color_from_settings
from .tmux_signals import fetch_tmux_signal_snapshot
from .tmux_signals import TmuxSignalEventWatcher
from .tmux_signals import window_record_key
from .types import AutoApproveState
from .types import AutoApproveStatusPayload
from .types import RunHistoryEntry
from .types import RunHistoryPayload
from .types import SearchResult
from .types import SessionFilesPayload
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
from .yoagent.streaming import YoagentStreamPublisher
from .yoagent.streaming import sanitized_stream_items as sanitized_yoagent_stream_items
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
ATTENTION_ACK_MAX_KEYS = 4096
ATTENTION_ACK_TTL_SECONDS = 7 * 24 * 3600
SESSION_FILES_CACHE_MAX_ITEMS = 64
SESSION_FILES_CACHE_SECONDS = 30.0
SESSION_FILES_CACHE_VERSION = 1
SESSION_FILES_CACHE_KEY_VERSION = 2
SESSION_FILES_CACHE_DIR = common.STATE_DIR / "session-files-cache"
SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
SESSION_FILES_DISK_CACHE_MAX_BYTES = 1024 * 1024 * 1024
SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS = 5 * 60
TABBER_ACTIVITY_CACHE_VERSION = 1
TABBER_ACTIVITY_CACHE_DIR = common.STATE_DIR / "activity-cache"
TABBER_ACTIVITY_CONSUMER_TTL_SECONDS = 30.0
TABBER_ACTIVITY_IDLE_REFRESH_SECONDS = 60.0
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
SERVER_INTERACTIVE_EVENT_POLL_SECONDS = 1.5
SERVER_INTERACTIVE_EVENT_POLL_JITTER_SECONDS = 0.5
SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS = SERVER_INTERACTIVE_EVENT_POLL_SECONDS
AUTO_APPROVE_CACHE_MAX_AGE_SECONDS = 5.003
SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS = SERVER_INTERACTIVE_EVENT_POLL_SECONDS
TMUX_SIGNAL_REMOVAL_EVENT_TTL_SECONDS = 10.0
INPUT_HEARTBEAT_COALESCE_SECONDS = 0.05
STATS_HISTORY_RETENTION_SECONDS = 24 * 60 * 60
STATS_HISTORY_RAW_WINDOW_SECONDS = 60 * 60
STATS_HISTORY_RAW_BUCKET_SECONDS = 1
STATS_HISTORY_ROLLUP_BUCKET_SECONDS = 30
STATS_HISTORY_POST_MAX_RECORDS = 1000
STATS_SAMPLE_CACHE_SECONDS = 0.95
STATS_HISTORY_SAMPLER_SECONDS = 1.0
STATS_AGENT_TOKEN_SAMPLE_SECONDS = 10.0
STATS_AGENT_TOKEN_BUCKET_SECONDS = 60.0
STATS_AGENT_TOKEN_CONSUMER_TTL_SECONDS = 6.0
STATS_SHARED_FRESH_SECONDS = 3.0
TMUX_AI_STATUS_VERSION = 1
STATS_HISTORY_CLIENT_ID_MAX_LENGTH = 96
STATS_HISTORY_CLIENT_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
STATS_HISTORY_BROWSER_FIELDS = (
    "api_count",
    "sse_count",
    "latency_total_ms",
    "latency_count",
    "bandwidth_bytes",
    "disconnected_ms",
)
STATS_HISTORY_SERVER_FIELDS = (
    "cpu_total_percent",
    "cpu_count",
    "system_cpu_total_percent",
    "system_cpu_count",
    "ask_agent_total",
    "run_agent_total",
    "transition_agent_total",
    "idle_agent_total",
    "active_agent_total",
    "inactive_agent_total",
    "agent_activity_samples",
    "tokens_per_agent_total",
    "agent_token_samples",
)
STATS_AGENT_ASK_STATES = frozenset({"approval", "needs-approval", "needs-input", "attention", "interrupted"})
STATS_AGENT_RUN_STATES = frozenset({"working"})
STATS_AGENT_TRANSITION_STATES = frozenset({"cooldown", "transition"})
STATS_AGENT_ACTIVE_STATES = STATS_AGENT_ASK_STATES | STATS_AGENT_RUN_STATES | STATS_AGENT_TRANSITION_STATES


def current_process_rss_bytes() -> int | None:
    try:
        statm = Path("/proc/self/statm").read_text(encoding="utf-8").split()
        resident_pages = int(statm[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        if resident_pages >= 0 and page_size > 0:
            return resident_pages * page_size
    except (OSError, ValueError, IndexError):
        pass
    try:
        max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, ValueError):
        return None
    if max_rss <= 0:
        return None
    return max_rss if sys.platform == "darwin" else max_rss * 1024


def clamp_cpu_percent(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(100.0, value))


def current_system_cpu_times() -> tuple[float, float] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    except (OSError, IndexError):
        return None
    if not fields or fields[0] != "cpu":
        return None
    try:
        values = [float(value) for value in fields[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0.0)
    total = sum(values)
    busy = total - idle
    return total, busy


def system_cpu_percent_from_times(previous: tuple[float, float] | None, current: tuple[float, float] | None) -> float:
    if previous is None or current is None:
        return 0.0
    total_delta = current[0] - previous[0]
    busy_delta = current[1] - previous[1]
    if total_delta <= 0 or busy_delta < 0:
        return 0.0
    return clamp_cpu_percent((busy_delta / total_delta) * 100.0)


def current_system_cpu_percent_from_ps() -> float | None:
    try:
        result = subprocess.run(["ps", "-A", "-o", "%cpu="], capture_output=True, text=True, timeout=0.75, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    total = 0.0
    found = False
    for line in result.stdout.splitlines():
        try:
            total += float(line.strip())
            found = True
        except ValueError:
            continue
    if not found:
        return None
    return clamp_cpu_percent(total / max(1, os.cpu_count() or 1))


def stats_history_empty_bucket(start: int, duration: int) -> dict[str, Any]:
    return {
        "start": start,
        "duration": duration,
        "sequence": 0,
        "server_sequence": 0,
        "api_count": 0.0,
        "sse_count": 0.0,
        "latency_total_ms": 0.0,
        "latency_count": 0.0,
        "bandwidth_bytes": 0.0,
        "disconnected_ms": 0.0,
        "cpu_total_percent": 0.0,
        "cpu_count": 0.0,
        "system_cpu_total_percent": 0.0,
        "system_cpu_count": 0.0,
        "ask_agent_total": 0.0,
        "run_agent_total": 0.0,
        "transition_agent_total": 0.0,
        "idle_agent_total": 0.0,
        "active_agent_total": 0.0,
        "inactive_agent_total": 0.0,
        "agent_activity_samples": 0.0,
        "tokens_per_agent_total": 0.0,
        "agent_token_samples": 0.0,
        "agent_token_rates": {},
        "clients": {},
    }


def stats_history_empty_client_bucket() -> dict[str, Any]:
    return {
        "sequence": 0,
        "api_count": 0.0,
        "sse_count": 0.0,
        "latency_total_ms": 0.0,
        "latency_count": 0.0,
        "bandwidth_bytes": 0.0,
        "disconnected_ms": 0.0,
    }


def stats_history_client_id(value: Any = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = STATS_HISTORY_CLIENT_ID_RE.sub("-", raw)
    return cleaned[:STATS_HISTORY_CLIENT_ID_MAX_LENGTH]


def stats_history_positive_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0:
        return 0.0
    return number


def stats_history_agent_token_rate_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        source = [{"key": key, **item} if isinstance(item, dict) else {"key": key, "total": item} for key, item in value.items()]
    elif isinstance(value, list):
        source = value
    else:
        return []
    records: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        label = str(item.get("label") or key).strip() or key
        total = stats_history_positive_number(item.get("total", item.get("rate", item.get("value"))))
        samples = stats_history_positive_number(item.get("samples"))
        tokens = stats_history_positive_number(item.get("tokens", item.get("token_total")))
        seconds = stats_history_positive_number(item.get("seconds", item.get("duration_seconds")))
        source_label = str(item.get("source") or "").strip()
        if tokens and not total:
            total = tokens
        if total and not samples:
            samples = 1.0
        if not total and not samples and not tokens:
            continue
        record = {"key": key, "label": label, "total": total, "samples": samples}
        if tokens:
            record["tokens"] = tokens
        if seconds:
            record["seconds"] = seconds
        if source_label:
            record["source"] = source_label
        records.append(record)
    return records


TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS = 1.009
TMUX_SIGNAL_ACTIVITY_WINDOW_SECONDS = 120.0
SERVER_WATCHED_PR_EVENT_POLL_SECONDS = 60.0
SERVER_ACTIVITY_HEARTBEAT_ROTATE_SECONDS = 3600.0
CLIENT_WATCH_ROOT_TTL_SECONDS = 300
CLIENT_WATCH_ROOT_LIMIT = 128
CLIENT_WATCH_FILE_LIMIT = 128
FILESYSTEM_WATCH_HISTORY_LIMIT = 64
FILESYSTEM_WATCH_HISTORY_SECONDS = 180.0
FILESYSTEM_WATCH_KEYFRAME_SECONDS = 60.0
PERFORMANCE_RECORD_LIMIT = 4096
PERFORMANCE_RECENT_LIMIT = 120
PERFORMANCE_SUMMARY_WINDOW_SECONDS = 60.0
BACKGROUND_REFRESH_EVENT_LOG_SAMPLE_EVERY = 25
BACKGROUND_CLIENT_EVENTS_PATH = common.STATE_DIR / "background-owner" / "client-events.json"
BACKGROUND_CLIENT_EVENT_TYPES = frozenset({"attention_acks_changed", "background_owner_changed", "background_refresh_done"})
BACKGROUND_CLIENT_EVENT_MANIFEST_LIMIT = 128
BACKGROUND_CLIENT_EVENT_NOTIFY_TIMEOUT_SECONDS = 0.2
CLIENT_EVENT_SIGNATURE_VOLATILE_KEYS = frozenset({
    "activity_age_seconds",
    "activity_ts",
    "cache",
    "compute_ms",
    "display_elapsed_seconds",
    "generated_at",
    "generated_ts",
    "history_bytes",
    "history_size",
    "last_counter_seen_at",
    "idle_since",
    "last_active_ts",
    "observed_ts",
    "screen_text",
    "session_activity_ts",
    "session_last_attached_ts",
    "server_time",
    "server_uptime_seconds",
    "status_counter_advanced",
    "status_elapsed_seconds",
    "status_identity",
    "status_line",
    "status_marker",
    "status_tokens",
    "metadata_badge_pulse_remaining_ms",
    "timings",
    "title",
    "working_elapsed_seconds",
})
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


@dataclass
class PendingInputHeartbeat:
    session: str
    source: str
    byte_count: int
    ts: float


def session_files_batch_worker_count(count: int) -> int:
    return max(1, min(SESSION_FILES_BATCH_MAX_WORKERS, count))


def add_phase_timing(timings: dict[str, float] | None, key: str, started: float) -> None:
    if timings is None:
        return
    timings[key] = round(float(timings.get(key) or 0.0) + (time.perf_counter() - started) * 1000, 1)


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
        self.owner_dir = self.path.with_name(f"{self.path.name}.owners")
        owner_digest = hashlib.sha256(self.owner_id.encode("utf-8", errors="replace")).hexdigest()[:24]
        self.owner_path = self.owner_dir / f"{owner_digest}.json"

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

    def _empty_owner_payload(self) -> dict[str, Any]:
        return {"version": 2, "owner_id": self.owner_id, "entries": {}, "updated_at": self._clock()}

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

    def _read_owner_payload(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            legacy_owner = self._read_payload().get("owners", {}).get(self.owner_id, {})
            if isinstance(legacy_owner, dict) and isinstance(legacy_owner.get("entries"), dict):
                return {
                    "version": 2,
                    "owner_id": self.owner_id,
                    "entries": legacy_owner["entries"],
                    "updated_at": self._clock(),
                }
            return self._empty_owner_payload()
        if not isinstance(raw, dict):
            return self._empty_owner_payload()
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            entries = {}
        return {
            "version": 2,
            "owner_id": str(raw.get("owner_id") or self.owner_id),
            "entries": entries,
            "updated_at": self._clock(),
        }

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

    def _write_owner_payload(self, payload: dict[str, Any]) -> None:
        owner_payload = {
            "version": 2,
            "owner_id": self.owner_id,
            "entries": payload.get("entries") if isinstance(payload.get("entries"), dict) else {},
            "updated_at": self._clock(),
        }
        atomic_write_text(self.owner_path, json.dumps(owner_payload, separators=(",", ":"), sort_keys=True), mode=0o600)

    def update_client_roots(self, roots: list[str]) -> None:
        now = self._clock()
        expires_at = now + self.ttl_seconds
        with file_lock(self.owner_path):
            payload = self._read_owner_payload()
            entries = {
                key: entry
                for key, entry in self._live_owner_entries(payload.get("entries", {}), now).items()
                if isinstance(entry, dict) and entry.get("source") != "client"
            }
            for path in roots[: self.limit]:
                entries[f"client:{path}"] = self._entry(path, "client", expires_at)
            payload["entries"] = entries
            self._write_owner_payload(payload)

    def update_active_roots(self, roots_by_session: dict[str, str]) -> None:
        now = self._clock()
        expires_at = now + self.ttl_seconds
        with file_lock(self.owner_path):
            payload = self._read_owner_payload()
            entries = {
                key: entry
                for key, entry in self._live_owner_entries(payload.get("entries", {}), now).items()
                if isinstance(entry, dict) and entry.get("source") != "active"
            }
            for session, path in sorted(roots_by_session.items()):
                if not path.startswith("/"):
                    continue
                entries[f"active:{session}:{path}"] = self._entry(path, "active", expires_at, session=session)
            payload["entries"] = entries
            self._write_owner_payload(payload)

    def snapshot(self) -> list[str]:
        now = self._clock()
        owners: dict[str, Any] = {}
        legacy_owners = self._read_payload().get("owners")
        if isinstance(legacy_owners, dict):
            owners.update(legacy_owners)
        try:
            owner_files = sorted(self.owner_dir.glob("*.json"))
        except OSError:
            owner_files = []
        for owner_file in owner_files:
            try:
                raw = json.loads(owner_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(raw, dict):
                continue
            entries = raw.get("entries")
            if not isinstance(entries, dict):
                continue
            owner_id = str(raw.get("owner_id") or owner_file.stem)
            owners[owner_id] = {"entries": entries, "updated_at": raw.get("updated_at")}
        if not owners:
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


def transcript_cache_identity(transcript: str | None) -> tuple[str, int, int]:
    if not transcript:
        return ("", 0, 0)
    path = Path(transcript).expanduser()
    try:
        resolved = str(path.resolve(strict=False))
    except OSError:
        resolved = str(path)
    try:
        stat = path.stat()
    except OSError:
        return (resolved, 0, 0)
    return (resolved, int(stat.st_dev), int(stat.st_ino))


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


def filesystem_signature_root_map(signature: tuple[Any, ...] | None) -> dict[str, tuple[Any, ...]]:
    result: dict[str, tuple[Any, ...]] = {}
    for item in signature or ():
        if not isinstance(item, tuple) or len(item) < 2 or not isinstance(item[1], tuple):
            continue
        result[str(item[0])] = item[1]
    return result


def filesystem_changed_roots(previous: tuple[Any, ...] | None, current: tuple[Any, ...] | None) -> tuple[list[str], list[str]]:
    previous_by_root = filesystem_signature_root_map(previous)
    current_by_root = filesystem_signature_root_map(current)
    changed = sorted(
        root
        for root, current_signature in current_by_root.items()
        if previous_by_root.get(root) != current_signature
    )
    removed = sorted(set(previous_by_root) - set(current_by_root))
    return changed, removed


def filesystem_change_summary(previous: tuple[Any, ...] | None, current: tuple[Any, ...] | None) -> dict[str, Any]:
    previous_by_root = filesystem_signature_root_map(previous)
    current_by_root = filesystem_signature_root_map(current)
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
    return (
        agent.kind or "",
        agent.cwd or "",
        agent.status or "",
        agent.session_id or "",
        agent.model or "",
        transcript_cache_identity(agent.transcript),
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


def cached_agent_auth_status_snapshot() -> dict[str, dict[str, object]]:
    try:
        return agent_auth_status(block=False, allow_stale=True, refresh=True)
    except TypeError:
        return agent_auth_status()


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


def patch_updates_active_color(patch: Any) -> bool:
    appearance = patch.get("appearance") if isinstance(patch, dict) else None
    return isinstance(appearance, dict) and "active_color" in appearance


def requires_known_session(refresh: bool = False, maintenance: bool = True) -> Callable[[Callable[..., tuple[Any, HTTPStatus]]], Callable[..., tuple[Any, HTTPStatus]]]:
    def decorator(func: Callable[..., tuple[Any, HTTPStatus]]) -> Callable[..., tuple[Any, HTTPStatus]]:
        @wraps(func)
        def wrapper(self: Any, session: str, *args: Any, **kwargs: Any) -> tuple[Any, HTTPStatus]:
            if refresh:
                self.refresh_sessions(maintenance=maintenance)
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
        self.input_heartbeat_condition = threading.Condition()
        self.input_heartbeat_pending: dict[tuple[str, str], PendingInputHeartbeat] = {}
        self.input_heartbeat_flush_active = False
        self.input_heartbeat_worker_stop = False
        self.input_heartbeat_worker_thread: threading.Thread | None = None
        self.session_files_cache_lock = threading.RLock()
        self.session_files_cache: dict[tuple[Any, ...], tuple[float, tuple[SessionFilesPayload, HTTPStatus]]] = {}
        self.session_files_refreshing_cache_keys: set[tuple[Any, ...]] = set()
        self.session_files_disk_prune_lock = threading.Lock()
        self.session_files_disk_prune_next_at = 0.0
        self.session_files_disk_prune_running = False
        self.session_files_disk_prune_last_result: dict[str, Any] = {}
        self.tabber_activity_cache_lock = threading.RLock()
        self.tabber_activity_cache: tuple[float, dict[str, Any]] | None = None
        self.tabber_activity_cache_source_signature = ""
        self.tabber_activity_cache_refreshing = False
        self.tabber_activity_cache_warmer_thread: threading.Thread | None = None
        self.tabber_activity_cache_warmer_running = False
        self.tabber_activity_consumer_until = 0.0
        self.auto_approve_cache_lock = threading.RLock()
        self.auto_approve_cache_condition = threading.Condition(self.auto_approve_cache_lock)
        self.auto_approve_cache: tuple[float, tuple[AutoApproveStatusPayload, HTTPStatus]] | None = None
        self.auto_approve_cache_refreshing = False
        self.tmux_signal_cache = TtlCache(TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS, max_entries=1)
        self.tmux_signal_event_watcher: TmuxSignalEventWatcher | None = None
        self.client_watch_tmux_signal_payload: dict[str, Any] | None = None
        self.tmux_snapshot_history_lock = threading.RLock()
        self.tmux_snapshot_history_signatures: dict[tuple[str, str, int], tuple[int, int]] = {}
        # last-logged watched-PR truncation state, so the cap is logged only when it changes.
        self._watched_pr_truncated_signature: tuple[int, tuple[str, ...]] | None = None
        self.metadata_warm_lock = threading.Lock()
        self.metadata_warm_running = False
        self.metadata_badge_lock = threading.Lock()
        self.metadata_badge_signatures: dict[str, dict[str, str]] = {}
        self.metadata_badge_pulse_until: dict[str, dict[str, float]] = {}
        self.stats_sample_lock = threading.Lock()
        self.stats_sample_last_monotonic: float | None = None
        self.stats_sample_last_process_time: float | None = None
        self.stats_sample_last_system_cpu_times: tuple[float, float] | None = None
        self.stats_sample_cached_monotonic: float | None = None
        self.stats_sample_cached_payload: dict[str, Any] | None = None
        self.stats_history_sampler_lock = threading.Lock()
        self.stats_history_sampler_thread: threading.Thread | None = None
        self.stats_history_sampler_stop_event = threading.Event()
        self.stats_history_sampler_running = False
        self.stats_history_lock = threading.RLock()
        self.stats_history_raw_buckets: dict[tuple[int, int], dict[str, Any]] = {}
        self.stats_history_rollup_buckets: dict[tuple[int, int], dict[str, Any]] = {}
        self.stats_history_sequence = 0
        self.stats_history_shared_rev = -1
        self.stats_history_shared_loaded = False
        self.attention_ack_lock = threading.RLock()
        self.attention_ack_keys: dict[str, float] = {}
        self.stats_agent_token_lock = threading.Lock()
        self.stats_agent_token_state: dict[str, dict[str, Any]] = {}
        self.stats_agent_activity_state: dict[str, dict[str, Any]] = {}
        self.stats_agent_token_next_sample_at = 0.0
        self.stats_agent_token_consumer_until = 0.0
        self.performance_record_lock = threading.RLock()
        self.performance_records: collections.deque[dict[str, Any]] = collections.deque(maxlen=PERFORMANCE_RECORD_LIMIT)
        self.background_refresh_event_log_lock = threading.Lock()
        self.background_refresh_event_log_counts: dict[tuple[str, str], int] = {}
        self.background_refresh_event_log_last_emit_counts: dict[tuple[str, str], int] = {}
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
        self.client_watch_transcript_content_signature: tuple[Any, ...] | None = None
        self.client_watch_filesystem_signature: tuple[Any, ...] | None = None
        self.client_watch_file_signature: tuple[Any, ...] | None = None
        self.client_watch_background_file_signature: tuple[Any, ...] | None = None
        self.client_watch_auto_approve_signature: str = ""
        self.client_watch_attention_ack_rev: int = -1
        self.client_watch_tmux_signal_signature: str = ""
        self.client_watch_tmux_signal_removal_event: dict[str, Any] = {}
        self.client_watch_watched_prs_signature: str = ""
        self.client_watch_context_item_payload_signatures: dict[str, str] = {}
        self.client_watch_session_file_payload_signatures: dict[str, str] = {}
        self.client_watch_transcripts_payload_signature: str = ""
        self.client_watch_activity_summary_signature: str = ""
        self.client_watch_filesystem_payload_signature: str = ""
        self.client_watch_filesystem_history: list[dict[str, Any]] = []
        self.client_watch_filesystem_last_full_at = 0.0
        self.client_watch_snapshot_running = False
        self.client_directory_poll_running = False
        self.tmux_theme_color = ""
        self.client_watch_thread: threading.Thread | None = None
        self.client_watch_running = False
        self.client_watch_wake_event = threading.Event()
        self.client_watch_stop_event = threading.Event()
        self.client_event_next_signature_poll_at = 0.0
        self.client_event_next_file_poll_at = 0.0
        self.client_event_next_background_file_poll_at = 0.0
        self.client_event_next_auto_poll_at = 0.0
        self.client_event_next_attention_ack_poll_at = 0.0
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
        self.yoagent_streams = YoagentStreamPublisher(
            publish_client_event=lambda *args, **kwargs: self.publish_client_event(*args, **kwargs),
            publish_stream_delta=lambda *args, **kwargs: self.publish_yoagent_stream_delta(*args, **kwargs),
        )
        self.yoagent_stream_lock = self.yoagent_streams.store.lock
        self.yoagent_stream_states = self.yoagent_streams.store.states
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
        self.background_owner: BackgroundOwnerRegistry | DisabledBackgroundOwner = DisabledBackgroundOwner()
        file_index.set_background_owner_checker(self.background_can_run)
        file_index.set_background_owner_refresh_requester(self.request_background_refresh)
        file_index.set_background_owner_bytes_recorder(self.record_background_search_index_bytes_written)
        file_index.set_background_owner_done_notifier(self.publish_background_refresh_done)

    def require_known_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus] | None:
        # The standard "unknown session -> 404" guard. Decorated handlers use requires_known_session();
        # payload-driven helpers and non-HTTP response shapes keep explicit checks.
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        return None

    def stats_history_next_sequence_locked(self) -> int:
        self.stats_history_sequence += 1
        return self.stats_history_sequence

    def stats_history_mark_server_changed_locked(self, bucket: dict[str, Any]) -> None:
        sequence = self.stats_history_next_sequence_locked()
        bucket["sequence"] = max(int(bucket.get("sequence") or 0), sequence)
        bucket["server_sequence"] = max(int(bucket.get("server_sequence") or 0), sequence)

    def stats_history_mark_client_changed_locked(self, bucket: dict[str, Any], client_bucket: dict[str, Any]) -> None:
        sequence = self.stats_history_next_sequence_locked()
        bucket["sequence"] = max(int(bucket.get("sequence") or 0), sequence)
        client_bucket["sequence"] = max(int(client_bucket.get("sequence") or 0), sequence)

    def stats_history_bucket_locked(self, sample_time: float, now: float) -> dict[str, Any] | None:
        if not math.isfinite(sample_time) or sample_time < now - STATS_HISTORY_RETENTION_SECONDS:
            return None
        if sample_time < now - STATS_HISTORY_RAW_WINDOW_SECONDS:
            bucket_seconds = STATS_HISTORY_ROLLUP_BUCKET_SECONDS
            buckets = self.stats_history_rollup_buckets
        else:
            bucket_seconds = STATS_HISTORY_RAW_BUCKET_SECONDS
            buckets = self.stats_history_raw_buckets
        start = int(math.floor(sample_time / bucket_seconds) * bucket_seconds)
        key = (start, bucket_seconds)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = stats_history_empty_bucket(start, bucket_seconds)
            buckets[key] = bucket
        return bucket

    def stats_history_client_bucket_locked(self, bucket: dict[str, Any], client_id: str) -> dict[str, Any]:
        clients = bucket.get("clients")
        if not isinstance(clients, dict):
            clients = {}
            bucket["clients"] = clients
        client_bucket = clients.get(client_id)
        if not isinstance(client_bucket, dict):
            client_bucket = stats_history_empty_client_bucket()
            clients[client_id] = client_bucket
        return client_bucket

    def stats_history_merge_agent_token_rates_locked(self, bucket: dict[str, Any], rates: Any) -> bool:
        changed = False
        token_rates = bucket.get("agent_token_rates")
        if not isinstance(token_rates, dict):
            token_rates = {}
            bucket["agent_token_rates"] = token_rates
        for item in stats_history_agent_token_rate_records(rates):
            key = item["key"]
            existing = token_rates.get(key)
            if not isinstance(existing, dict):
                existing = {"label": item["label"], "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0}
                token_rates[key] = existing
            existing["label"] = item["label"] or existing.get("label") or key
            existing["total"] = float(existing.get("total") or 0.0) + float(item.get("total") or 0.0)
            existing["samples"] = float(existing.get("samples") or 0.0) + float(item.get("samples") or 0.0)
            existing["tokens"] = float(existing.get("tokens") or 0.0) + float(item.get("tokens") or 0.0)
            existing["seconds"] = float(existing.get("seconds") or 0.0) + float(item.get("seconds") or 0.0)
            if item.get("source"):
                existing["source"] = str(item.get("source") or "")
            changed = True
        return changed

    def stats_history_merge_record_locked(
        self,
        record: dict[str, Any],
        now: float,
        fields: tuple[str, ...] = STATS_HISTORY_BROWSER_FIELDS,
        client_id: str | None = "",
    ) -> None:
        sample_time = record.get("start", record.get("time", now))
        try:
            sample_time_float = float(sample_time)
        except (TypeError, ValueError):
            sample_time_float = now
        bucket = self.stats_history_bucket_locked(sample_time_float, now)
        if bucket is None:
            return
        changed = False
        if client_id is None:
            for key in fields:
                value = stats_history_positive_number(record.get(key))
                if value:
                    bucket[key] = float(bucket.get(key) or 0.0) + value
                    changed = True
            if "agent_token_samples" in fields:
                changed = self.stats_history_merge_agent_token_rates_locked(bucket, record.get("agent_token_rates")) or changed
            if changed:
                self.stats_history_mark_server_changed_locked(bucket)
            return
        client_bucket = self.stats_history_client_bucket_locked(bucket, stats_history_client_id(client_id))
        for key in fields:
            value = stats_history_positive_number(record.get(key))
            if value:
                client_bucket[key] = float(client_bucket.get(key) or 0.0) + value
                changed = True
        if changed:
            self.stats_history_mark_client_changed_locked(bucket, client_bucket)

    def stats_history_merge_bucket_locked(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        for key in STATS_HISTORY_SERVER_FIELDS:
            target[key] = float(target.get(key) or 0.0) + float(source.get(key) or 0.0)
        self.stats_history_merge_agent_token_rates_locked(target, source.get("agent_token_rates"))
        target["server_sequence"] = max(int(target.get("server_sequence") or 0), int(source.get("server_sequence") or 0))
        target["sequence"] = max(int(target.get("sequence") or 0), int(source.get("sequence") or 0), int(target.get("server_sequence") or 0))
        source_clients = source.get("clients")
        if isinstance(source_clients, dict):
            for client_id, source_client in source_clients.items():
                if not isinstance(source_client, dict):
                    continue
                target_client = self.stats_history_client_bucket_locked(target, str(client_id))
                for key in STATS_HISTORY_BROWSER_FIELDS:
                    target_client[key] = float(target_client.get(key) or 0.0) + float(source_client.get(key) or 0.0)
                target_client["sequence"] = max(int(target_client.get("sequence") or 0), int(source_client.get("sequence") or 0))
                target["sequence"] = max(int(target.get("sequence") or 0), int(target_client.get("sequence") or 0))

    def stats_history_compact_locked(self, now: float) -> None:
        raw_cutoff = now - STATS_HISTORY_RAW_WINDOW_SECONDS
        for key, bucket in list(self.stats_history_raw_buckets.items()):
            if float(bucket.get("start") or 0.0) >= raw_cutoff:
                continue
            start = int(math.floor(float(bucket.get("start") or 0.0) / STATS_HISTORY_ROLLUP_BUCKET_SECONDS) * STATS_HISTORY_ROLLUP_BUCKET_SECONDS)
            rollup_key = (start, STATS_HISTORY_ROLLUP_BUCKET_SECONDS)
            rollup = self.stats_history_rollup_buckets.get(rollup_key)
            if rollup is None:
                rollup = stats_history_empty_bucket(start, STATS_HISTORY_ROLLUP_BUCKET_SECONDS)
                self.stats_history_rollup_buckets[rollup_key] = rollup
            self.stats_history_merge_bucket_locked(rollup, bucket)
            self.stats_history_raw_buckets.pop(key, None)
        retention_cutoff = now - STATS_HISTORY_RETENTION_SECONDS
        for key, bucket in list(self.stats_history_raw_buckets.items()):
            if float(bucket.get("start") or 0.0) < retention_cutoff:
                self.stats_history_raw_buckets.pop(key, None)
        for key, bucket in list(self.stats_history_rollup_buckets.items()):
            if float(bucket.get("start") or 0.0) < retention_cutoff:
                self.stats_history_rollup_buckets.pop(key, None)

    def stats_history_record_client_bucket_locked(self, bucket: dict[str, Any], client_id: str) -> dict[str, Any] | None:
        clients = bucket.get("clients")
        if isinstance(clients, dict):
            client_bucket = clients.get(client_id)
            if isinstance(client_bucket, dict):
                return client_bucket
        if client_id == "" and not isinstance(clients, dict):
            return bucket
        return None

    def stats_history_records_locked(self, since: int = 0, client_id: str = "") -> list[dict[str, Any]]:
        records = []
        clean_client_id = stats_history_client_id(client_id)
        for bucket in [*self.stats_history_rollup_buckets.values(), *self.stats_history_raw_buckets.values()]:
            client_bucket = self.stats_history_record_client_bucket_locked(bucket, clean_client_id)
            server_sequence = int(bucket.get("server_sequence") or 0)
            client_sequence = int(client_bucket.get("sequence") or 0) if isinstance(client_bucket, dict) else 0
            sequence = max(server_sequence, client_sequence)
            if sequence <= since:
                continue
            records.append({
                "start": int(bucket.get("start") or 0),
                "duration": int(bucket.get("duration") or 0),
                "sequence": sequence,
                "api_count": float(client_bucket.get("api_count") or 0.0) if isinstance(client_bucket, dict) else 0.0,
                "sse_count": float(client_bucket.get("sse_count") or 0.0) if isinstance(client_bucket, dict) else 0.0,
                "latency_total_ms": float(client_bucket.get("latency_total_ms") or 0.0) if isinstance(client_bucket, dict) else 0.0,
                "latency_count": float(client_bucket.get("latency_count") or 0.0) if isinstance(client_bucket, dict) else 0.0,
                "bandwidth_bytes": float(client_bucket.get("bandwidth_bytes") or 0.0) if isinstance(client_bucket, dict) else 0.0,
                "disconnected_ms": float(client_bucket.get("disconnected_ms") or 0.0) if isinstance(client_bucket, dict) else 0.0,
                "cpu_total_percent": float(bucket.get("cpu_total_percent") or 0.0),
                "cpu_count": float(bucket.get("cpu_count") or 0.0),
                "system_cpu_total_percent": float(bucket.get("system_cpu_total_percent") or 0.0),
                "system_cpu_count": float(bucket.get("system_cpu_count") or 0.0),
                "ask_agent_total": float(bucket.get("ask_agent_total") or 0.0),
                "run_agent_total": float(bucket.get("run_agent_total") or 0.0),
                "transition_agent_total": float(bucket.get("transition_agent_total") or 0.0),
                "idle_agent_total": float(bucket.get("idle_agent_total") or 0.0),
                "active_agent_total": float(bucket.get("active_agent_total") or 0.0),
                "inactive_agent_total": float(bucket.get("inactive_agent_total") or 0.0),
                "agent_activity_samples": float(bucket.get("agent_activity_samples") or 0.0),
                "tokens_per_agent_total": float(bucket.get("tokens_per_agent_total") or 0.0),
                "agent_token_samples": float(bucket.get("agent_token_samples") or 0.0),
                "agent_token_rates": [
                    {
                        "key": key,
                        "label": str(item.get("label") or key),
                        "total": float(item.get("total") or 0.0),
                        "samples": float(item.get("samples") or 0.0),
                        "tokens": float(item.get("tokens") or 0.0),
                        "seconds": float(item.get("seconds") or 0.0),
                        "source": str(item.get("source") or ""),
                    }
                    for key, item in sorted((bucket.get("agent_token_rates") or {}).items())
                    if isinstance(item, dict)
                ],
            })
        return sorted(records, key=lambda item: (item["start"], item["duration"], item["sequence"]))

    def stats_history_payload_locked(self, since: int = 0, client_id: str = "") -> dict[str, Any]:
        return {
            "sequence": self.stats_history_sequence,
            "records": self.stats_history_records_locked(since, client_id=client_id),
            "retention_seconds": STATS_HISTORY_RETENTION_SECONDS,
            "raw_window_seconds": STATS_HISTORY_RAW_WINDOW_SECONDS,
            "rollup_bucket_seconds": STATS_HISTORY_ROLLUP_BUCKET_SECONDS,
            "client_id": stats_history_client_id(client_id),
        }

    def tmux_ai_status_empty(self) -> dict[str, Any]:
        return {
            "version": TMUX_AI_STATUS_VERSION,
            "rev": 0,
            "updated_at": 0.0,
            "attention_acks": {"rev": 0, "updated_at": 0.0, "keys": {}},
            "stats_history": {
                "rev": 0,
                "updated_at": 0.0,
                "sequence": 0,
                "sample": {},
                "raw_buckets": [],
                "rollup_buckets": [],
                "agent_token_state": {},
                "agent_activity_state": {},
                "agent_token_next_sample_at": 0.0,
                "agent_token_consumer_until": 0.0,
                "writer": {},
            },
        }

    def _read_shared_tmux_ai_status_locked(self) -> dict[str, Any]:
        status = self.tmux_ai_status_empty()
        try:
            data = json.loads(common.TMUX_AI_STATUS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            legacy = json.loads(common.LEGACY_ATTENTION_ACKS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            legacy = {}
        if not isinstance(legacy, dict):
            legacy = {}
        if not data and legacy:
            data = {
                "version": TMUX_AI_STATUS_VERSION,
                "rev": legacy.get("rev", 0),
                "attention_acks": {
                    "rev": legacy.get("rev", 0),
                    "updated_at": legacy.get("updated_at", 0.0),
                    "keys": legacy.get("keys", {}),
                },
            }
        try:
            status["rev"] = max(0, int(data.get("rev", 0)))
        except (TypeError, ValueError):
            status["rev"] = 0
        try:
            status["updated_at"] = max(0.0, float(data.get("updated_at", 0.0)))
        except (TypeError, ValueError):
            status["updated_at"] = 0.0
        attention = data.get("attention_acks") if isinstance(data.get("attention_acks"), dict) else {}
        if not attention and isinstance(data.get("keys"), dict):
            attention = {"rev": data.get("rev", 0), "updated_at": data.get("updated_at", 0.0), "keys": data.get("keys", {})}
        legacy_keys = legacy.get("keys") if isinstance(legacy.get("keys"), dict) else {}
        if legacy_keys:
            attention = dict(attention) if isinstance(attention, dict) else {}
            merged_keys = dict(attention.get("keys")) if isinstance(attention.get("keys"), dict) else {}
            for key, ts in legacy_keys.items():
                try:
                    legacy_ts = float(ts)
                except (TypeError, ValueError):
                    continue
                try:
                    existing_ts = float(merged_keys.get(str(key)) or 0.0)
                except (TypeError, ValueError):
                    existing_ts = 0.0
                merged_keys[str(key)] = max(existing_ts, legacy_ts)
            attention["keys"] = merged_keys
            try:
                attention["rev"] = max(int(attention.get("rev") or 0), int(legacy.get("rev") or 0))
            except (TypeError, ValueError):
                attention["rev"] = 0
            attention["legacy_rev"] = legacy.get("rev", 0)
        status["attention_acks"] = attention if isinstance(attention, dict) else {}
        stats_history = data.get("stats_history") if isinstance(data.get("stats_history"), dict) else {}
        status["stats_history"] = stats_history if isinstance(stats_history, dict) else {}
        return status

    def _write_shared_tmux_ai_status_locked(self, status: dict[str, Any]) -> int:
        now = time.time()
        try:
            rev = max(0, int(status.get("rev", 0))) + 1
        except (TypeError, ValueError):
            rev = 1
        payload = dict(status)
        payload["version"] = TMUX_AI_STATUS_VERSION
        payload["rev"] = rev
        payload["updated_at"] = now
        atomic_write_text(
            common.TMUX_AI_STATUS_PATH,
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )
        return rev

    def stats_history_uses_shared_status(self) -> bool:
        return not isinstance(self.background_owner, DisabledBackgroundOwner)

    def stats_history_bucket_has_server_data(self, bucket: dict[str, Any]) -> bool:
        if int(bucket.get("server_sequence") or 0) > 0:
            return True
        if any(float(bucket.get(key) or 0.0) for key in STATS_HISTORY_SERVER_FIELDS):
            return True
        return bool(bucket.get("agent_token_rates"))

    def stats_history_agent_token_rates_snapshot(self, rates: Any) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for item in stats_history_agent_token_rate_records(rates):
            snapshot[item["key"]] = {
                "label": item["label"],
                "total": float(item.get("total") or 0.0),
                "samples": float(item.get("samples") or 0.0),
                "tokens": float(item.get("tokens") or 0.0),
                "seconds": float(item.get("seconds") or 0.0),
                "source": str(item.get("source") or ""),
            }
        return snapshot

    def stats_history_shared_bucket_snapshot(self, bucket: dict[str, Any]) -> dict[str, Any]:
        snapshot = {
            "start": int(bucket.get("start") or 0),
            "duration": int(bucket.get("duration") or 0),
            "sequence": int(bucket.get("server_sequence") or bucket.get("sequence") or 0),
            "server_sequence": int(bucket.get("server_sequence") or bucket.get("sequence") or 0),
            "agent_token_rates": self.stats_history_agent_token_rates_snapshot(bucket.get("agent_token_rates")),
        }
        for key in STATS_HISTORY_SERVER_FIELDS:
            snapshot[key] = float(bucket.get(key) or 0.0)
        return snapshot

    def stats_history_shared_agent_state_snapshot(self, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key or "").strip()
            if not key or not isinstance(raw_item, dict):
                continue
            item: dict[str, Any] = {}
            for field in ("tokens", "time", "transition_started"):
                try:
                    item[field] = float(raw_item.get(field) or 0.0)
                except (TypeError, ValueError):
                    item[field] = 0.0
            for field in ("label", "source", "state", "kind"):
                text = str(raw_item.get(field) or "").strip()
                if text:
                    item[field] = text
            snapshot[key] = item
        return snapshot

    def stats_history_shared_snapshot(self, sample: dict[str, Any]) -> dict[str, Any]:
        now = float(sample.get("time") or time.time())
        with self.stats_history_lock:
            self.stats_history_compact_locked(now)
            raw_buckets = [
                self.stats_history_shared_bucket_snapshot(bucket)
                for bucket in self.stats_history_raw_buckets.values()
                if self.stats_history_bucket_has_server_data(bucket)
            ]
            rollup_buckets = [
                self.stats_history_shared_bucket_snapshot(bucket)
                for bucket in self.stats_history_rollup_buckets.values()
                if self.stats_history_bucket_has_server_data(bucket)
            ]
            sequence = self.stats_history_sequence
        with self.stats_agent_token_lock:
            token_state = self.stats_history_shared_agent_state_snapshot(self.stats_agent_token_state)
            activity_state = self.stats_history_shared_agent_state_snapshot(self.stats_agent_activity_state)
            token_next_sample_at = self.stats_agent_token_next_sample_at
            token_consumer_until = self.stats_agent_token_consumer_until
        return {
            "updated_at": now,
            "sequence": sequence,
            "sample": dict(sample),
            "raw_buckets": sorted(raw_buckets, key=lambda item: (item["start"], item["duration"])),
            "rollup_buckets": sorted(rollup_buckets, key=lambda item: (item["start"], item["duration"])),
            "agent_token_state": token_state,
            "agent_activity_state": activity_state,
            "agent_token_next_sample_at": token_next_sample_at,
            "agent_token_consumer_until": token_consumer_until,
            "writer": self.background_owner.owner_payload(),
        }

    def write_shared_stats_history(self, sample: dict[str, Any]) -> int:
        if not self.stats_history_uses_shared_status():
            return self.stats_history_shared_rev
        snapshot = self.stats_history_shared_snapshot(sample)
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            existing = status.get("stats_history") if isinstance(status.get("stats_history"), dict) else {}
            try:
                stats_rev = max(0, int(existing.get("rev", 0))) + 1
            except (TypeError, ValueError):
                stats_rev = 1
            snapshot["rev"] = stats_rev
            status["stats_history"] = snapshot
            self._write_shared_tmux_ai_status_locked(status)
        self.stats_history_shared_rev = stats_rev
        self.stats_history_shared_loaded = True
        return stats_rev

    def write_shared_stats_token_consumer_until(self, consumer_until: float) -> None:
        if not self.stats_history_uses_shared_status() or consumer_until <= 0:
            return
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            stats_history = status.get("stats_history") if isinstance(status.get("stats_history"), dict) else {}
            stats_history = dict(stats_history)
            try:
                previous = float(stats_history.get("agent_token_consumer_until") or 0.0)
            except (TypeError, ValueError):
                previous = 0.0
            if consumer_until <= previous:
                return
            try:
                stats_rev = max(0, int(stats_history.get("rev", 0))) + 1
            except (TypeError, ValueError):
                stats_rev = 1
            stats_history["rev"] = stats_rev
            stats_history["agent_token_consumer_until"] = consumer_until
            stats_history["consumer_updated_at"] = time.time()
            status["stats_history"] = stats_history
            self._write_shared_tmux_ai_status_locked(status)

    def stats_history_refresh_bucket_sequence_locked(self, bucket: dict[str, Any]) -> None:
        sequence = int(bucket.get("server_sequence") or 0)
        clients = bucket.get("clients")
        if isinstance(clients, dict):
            for client_bucket in clients.values():
                if isinstance(client_bucket, dict):
                    sequence = max(sequence, int(client_bucket.get("sequence") or 0))
        bucket["sequence"] = sequence

    def stats_history_clear_server_fields_locked(self) -> None:
        for bucket in [*self.stats_history_raw_buckets.values(), *self.stats_history_rollup_buckets.values()]:
            for key in STATS_HISTORY_SERVER_FIELDS:
                bucket[key] = 0.0
            bucket["agent_token_rates"] = {}
            bucket["server_sequence"] = 0
            self.stats_history_refresh_bucket_sequence_locked(bucket)

    def stats_history_apply_shared_bucket_locked(self, source: dict[str, Any]) -> None:
        try:
            start = int(source.get("start") or 0)
            duration = int(source.get("duration") or 0)
        except (TypeError, ValueError):
            return
        if start <= 0 or duration <= 0:
            return
        buckets = self.stats_history_rollup_buckets if duration == STATS_HISTORY_ROLLUP_BUCKET_SECONDS else self.stats_history_raw_buckets
        key = (start, duration)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = stats_history_empty_bucket(start, duration)
            buckets[key] = bucket
        for field in STATS_HISTORY_SERVER_FIELDS:
            bucket[field] = float(source.get(field) or 0.0)
        bucket["agent_token_rates"] = self.stats_history_agent_token_rates_snapshot(source.get("agent_token_rates"))
        server_sequence = int(source.get("server_sequence") or source.get("sequence") or 0)
        bucket["server_sequence"] = server_sequence
        self.stats_history_refresh_bucket_sequence_locked(bucket)

    def stats_history_apply_shared_agent_state(self, stats_history: dict[str, Any]) -> None:
        with self.stats_agent_token_lock:
            token_state = stats_history.get("agent_token_state")
            if isinstance(token_state, dict):
                self.stats_agent_token_state = self.stats_history_shared_agent_state_snapshot(token_state)
            activity_state = stats_history.get("agent_activity_state")
            if isinstance(activity_state, dict):
                self.stats_agent_activity_state = self.stats_history_shared_agent_state_snapshot(activity_state)
            try:
                self.stats_agent_token_next_sample_at = max(self.stats_agent_token_next_sample_at, float(stats_history.get("agent_token_next_sample_at") or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                self.stats_agent_token_consumer_until = max(self.stats_agent_token_consumer_until, float(stats_history.get("agent_token_consumer_until") or 0.0))
            except (TypeError, ValueError):
                pass

    def merge_shared_stats_history(self, max_age_seconds: float | None = None) -> dict[str, Any]:
        if not self.stats_history_uses_shared_status():
            return {"ok": False, "fresh": False, "sample": {}, "updated_at": 0.0, "rev": self.stats_history_shared_rev}
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
        stats_history = status.get("stats_history") if isinstance(status.get("stats_history"), dict) else {}
        if not stats_history:
            return {"ok": False, "fresh": False, "sample": {}, "updated_at": 0.0, "rev": self.stats_history_shared_rev}
        try:
            rev = max(0, int(stats_history.get("rev", 0)))
        except (TypeError, ValueError):
            rev = 0
        try:
            updated_at = max(0.0, float(stats_history.get("updated_at") or 0.0))
        except (TypeError, ValueError):
            updated_at = 0.0
        now = time.time()
        fresh = bool(updated_at and (max_age_seconds is None or now - updated_at <= max_age_seconds))
        if rev != self.stats_history_shared_rev:
            raw_buckets = stats_history.get("raw_buckets") if isinstance(stats_history.get("raw_buckets"), list) else []
            rollup_buckets = stats_history.get("rollup_buckets") if isinstance(stats_history.get("rollup_buckets"), list) else []
            with self.stats_history_lock:
                self.stats_history_clear_server_fields_locked()
                for bucket in [*rollup_buckets, *raw_buckets]:
                    if isinstance(bucket, dict):
                        self.stats_history_apply_shared_bucket_locked(bucket)
                try:
                    self.stats_history_sequence = max(self.stats_history_sequence, int(stats_history.get("sequence") or 0))
                except (TypeError, ValueError):
                    pass
                self.stats_history_compact_locked(now)
            self.stats_history_apply_shared_agent_state(stats_history)
            self.stats_history_shared_rev = rev
            self.stats_history_shared_loaded = True
        sample = stats_history.get("sample") if isinstance(stats_history.get("sample"), dict) else {}
        return {"ok": True, "fresh": fresh, "sample": dict(sample), "updated_at": updated_at, "rev": rev}

    def record_stats_history_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        if not isinstance(payload, dict):
            return {"error": "payload must be an object"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        client_id = stats_history_client_id(payload.get("client_id", payload.get("client", "")))
        with self.stats_history_lock:
            if payload.get("clear") is True:
                self.stats_history_raw_buckets.clear()
                self.stats_history_rollup_buckets.clear()
                self.stats_history_next_sequence_locked()
            records = payload.get("records", [])
            if records is None:
                records = []
            if not isinstance(records, list):
                return {"error": "records must be a list"}, HTTPStatus.BAD_REQUEST
            if len(records) > STATS_HISTORY_POST_MAX_RECORDS:
                return {"error": f"records limit is {STATS_HISTORY_POST_MAX_RECORDS}"}, HTTPStatus.BAD_REQUEST
            for record in records:
                if isinstance(record, dict):
                    self.stats_history_merge_record_locked(record, now, client_id=client_id)
            self.stats_history_compact_locked(now)
            try:
                since = int(payload.get("since") or 0)
            except (TypeError, ValueError):
                since = 0
            return {"ok": True, "history": self.stats_history_payload_locked(max(0, since), client_id=client_id)}, HTTPStatus.OK

    def stats_agent_window_rows(self) -> list[dict[str, Any]]:
        cached_payload = self.fresh_auto_approve_payload_for_stats()
        if cached_payload is not None:
            return self.stats_agent_window_rows_from_auto_approve_payload(cached_payload)
        self.refresh_sessions(maintenance=False)
        if not self.sessions:
            return []
        sessions, _errors = discover_sessions(self.sessions)
        if not sessions:
            return []
        activity_snapshot = self.activity_snapshot_with_recency()
        rows: list[dict[str, Any]] = []
        for session in self.sessions:
            info = sessions.get(session)
            if info is None:
                continue
            for row in self.agent_window_status_payloads(
                session,
                info=info,
                discovered_sessions=sessions,
                activity_snapshot=activity_snapshot,
                include_path_metadata=False,
            ):
                item = dict(row)
                item["session"] = session
                rows.append(item)
        return rows

    def fresh_auto_approve_payload_for_stats(self) -> AutoApproveStatusPayload | None:
        with self.auto_approve_cache_condition:
            cached = self.auto_approve_cache
            if cached is None:
                return None
            stored_at, (payload, status) = cached
            if status != HTTPStatus.OK:
                return None
            if time.monotonic() - stored_at > AUTO_APPROVE_CACHE_MAX_AGE_SECONDS:
                return None
            if not isinstance(payload, dict):
                return None
            return copy.deepcopy(payload)

    @staticmethod
    def stats_agent_window_rows_from_auto_approve_payload(payload: AutoApproveStatusPayload) -> list[dict[str, Any]]:
        sessions_payload = payload.get("sessions")
        if not isinstance(sessions_payload, dict):
            return []
        session_order = payload.get("session_order")
        ordered_sessions = [str(session) for session in session_order] if isinstance(session_order, list) else list(sessions_payload)
        rows: list[dict[str, Any]] = []
        for session in ordered_sessions:
            state = sessions_payload.get(session)
            if not isinstance(state, dict):
                continue
            for row in state.get("agent_windows") if isinstance(state.get("agent_windows"), list) else []:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                item["session"] = session
                rows.append(item)
        return rows

    def stats_agent_is_active(self, row: dict[str, Any]) -> bool:
        state = str(row.get("state") or "").strip().lower()
        return state in STATS_AGENT_ACTIVE_STATES

    def stats_agent_transition_seconds(self) -> float:
        return self.performance_setting_seconds("workflow_transition_glow_seconds", 0.0, 300.0)

    def stats_agent_cooldown_visible(self, row: dict[str, Any], sample_time: float, transition_seconds: float) -> bool:
        if row.get("cooldown_acknowledged") is True:
            return False
        stopped_ts = self.float_value(row.get("working_stopped_ts"), 0.0)
        if stopped_ts <= 0:
            return False
        return True

    def stats_agent_token_sample_seconds(self) -> float:
        return STATS_AGENT_TOKEN_SAMPLE_SECONDS

    def stats_agent_token_sampling_due(self, sample_time: float, token_consumer: bool = False) -> bool:
        with self.stats_agent_token_lock:
            if token_consumer:
                self.stats_agent_token_consumer_until = max(self.stats_agent_token_consumer_until, sample_time + STATS_AGENT_TOKEN_CONSUMER_TTL_SECONDS)
            if sample_time > self.stats_agent_token_consumer_until:
                return False
            if sample_time < self.stats_agent_token_next_sample_at:
                return False
            self.stats_agent_token_next_sample_at = sample_time + self.stats_agent_token_sample_seconds()
            return True

    def stats_agent_activity_kind_locked(self, row: dict[str, Any], key: str, sample_time: float, transition_seconds: float) -> str:
        state = str(row.get("state") or "").strip().lower()
        previous = self.stats_agent_activity_state.get(key) if key else None
        previous_kind = str(previous.get("kind") or "") if isinstance(previous, dict) else ""
        previous_transition_started = self.float_value(previous.get("transition_started") if isinstance(previous, dict) else 0.0, 0.0)
        transition_started = 0.0
        kind = "idle"
        if state in STATS_AGENT_ASK_STATES and row.get("attention_acknowledged") is not True:
            kind = "ask"
        elif state in STATS_AGENT_RUN_STATES:
            kind = "run"
        elif state in STATS_AGENT_TRANSITION_STATES and row.get("cooldown_acknowledged") is not True:
            kind = "transition"
            transition_started = self.float_value(row.get("working_stopped_ts"), 0.0) or previous_transition_started or sample_time
        elif self.stats_agent_cooldown_visible(row, sample_time, transition_seconds):
            kind = "transition"
            transition_started = self.float_value(row.get("working_stopped_ts"), 0.0)
        elif transition_seconds <= 0 and row.get("cooldown_acknowledged") is not True and previous_kind in {"run", "transition"}:
            kind = "transition"
            transition_started = previous_transition_started or sample_time
        elif transition_seconds > 0:
            if previous_kind == "run":
                transition_started = sample_time
            elif previous_kind == "transition":
                transition_started = previous_transition_started or sample_time
            if transition_started and sample_time - transition_started < transition_seconds:
                kind = "transition"
            else:
                transition_started = 0.0
        if key:
            self.stats_agent_activity_state[key] = {
                "state": state,
                "kind": kind,
                "time": sample_time,
                "transition_started": transition_started,
            }
        return kind

    def stats_agent_transcript_token_count(self, row: dict[str, Any]) -> float | None:
        transcript = str(row.get("transcript") or "").strip()
        kind = str(row.get("kind") or "").strip().lower()
        if not transcript:
            return None
        generated_tokens = session_files.transcript_generated_tokens(Path(transcript), kind)
        if generated_tokens is None:
            return None
        return generated_tokens

    def stats_agent_token_count(self, row: dict[str, Any]) -> float | None:
        row.pop("_stats_agent_token_source", None)
        transcript_tokens = self.stats_agent_transcript_token_count(row)
        if transcript_tokens is not None:
            row["_stats_agent_token_source"] = "transcript"
            return transcript_tokens
        return None

    def stats_agent_token_key(self, row: dict[str, Any], fallback_index: int) -> str:
        session = str(row.get("session") or "").strip()
        window = row.get("window_index")
        if not isinstance(window, int):
            window = str(row.get("window") or row.get("window_label") or row.get("label") or "").strip()
        kind = str(row.get("kind") or "").strip().lower()
        parts = [session, str(window).strip(), kind]
        key = "|".join(part for part in parts if part)
        return key or f"agent-{fallback_index}"

    def stats_agent_token_label(self, row: dict[str, Any]) -> str:
        session = str(row.get("session") or "").strip()
        window_label = str(row.get("window_label") or row.get("label") or row.get("window") or "").strip()
        kind = str(row.get("kind") or "agent").strip() or "agent"
        return ":".join(part for part in (session, window_label or kind) if part) or kind

    def stats_agent_token_delta_records(self, key: str, label: str, start_time: float, end_time: float, token_delta: float) -> list[dict[str, Any]]:
        if not key or not math.isfinite(start_time) or not math.isfinite(end_time) or end_time <= start_time:
            return []
        if not math.isfinite(token_delta) or token_delta <= 0:
            return []
        elapsed = end_time - start_time
        bucket_seconds = STATS_AGENT_TOKEN_BUCKET_SECONDS
        records: list[dict[str, Any]] = []
        cursor = start_time
        while cursor < end_time:
            bucket_start = math.floor(cursor / bucket_seconds) * bucket_seconds
            bucket_end = min(end_time, bucket_start + bucket_seconds)
            overlap = max(0.0, bucket_end - cursor)
            if overlap <= 0:
                cursor = min(end_time, cursor + bucket_seconds)
                continue
            tokens = token_delta * (overlap / elapsed)
            records.append({
                "time": bucket_start,
                "tokens_per_agent_total": tokens,
                "agent_token_samples": 1.0,
                "agent_token_rates": [{
                    "key": key,
                    "label": label,
                    "total": tokens,
                    "samples": 1.0,
                    "tokens": tokens,
                    "seconds": bucket_seconds,
                    "source": "transcript",
                }],
            })
            cursor = bucket_end
        return records

    def stats_agent_activity_record(self, sample_time: float, include_token_rates: bool = True) -> dict[str, Any] | None:
        rows = self.stats_agent_window_rows()
        if not rows:
            with self.stats_agent_token_lock:
                self.stats_agent_token_state.clear()
                self.stats_agent_activity_state.clear()
                self.stats_agent_token_next_sample_at = 0.0
                self.stats_agent_token_consumer_until = 0.0
            return None
        ask_agents = 0
        run_agents = 0
        transition_agents = 0
        idle_agents = 0
        inactive_agents = 0
        agent_token_records: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        transition_seconds = self.stats_agent_transition_seconds()
        with self.stats_agent_token_lock:
            for index, row in enumerate(rows):
                key = self.stats_agent_token_key(row, index)
                seen_keys.add(key)
                activity_kind = self.stats_agent_activity_kind_locked(row, key, sample_time, transition_seconds)
                if activity_kind == "ask":
                    ask_agents += 1
                elif activity_kind == "run":
                    run_agents += 1
                elif activity_kind == "transition":
                    transition_agents += 1
                else:
                    idle_agents += 1
                    inactive_agents += 1
                if include_token_rates:
                    token_count = self.stats_agent_token_count(row)
                    if token_count is None:
                        continue
                    token_source = str(row.get("_stats_agent_token_source") or "sample").strip() or "sample"
                    label = self.stats_agent_token_label(row)
                    previous = self.stats_agent_token_state.get(key)
                    previous_source = str(previous.get("source") or "") if isinstance(previous, dict) else ""
                    previous_tokens = float(previous.get("tokens") or 0.0) if isinstance(previous, dict) else 0.0
                    previous_time = float(previous.get("time") or 0.0) if isinstance(previous, dict) else 0.0
                    if previous and previous_source == token_source and token_count >= previous_tokens and sample_time > previous_time:
                        agent_token_records.extend(self.stats_agent_token_delta_records(key, label, previous_time, sample_time, token_count - previous_tokens))
                    self.stats_agent_token_state[key] = {"tokens": token_count, "time": sample_time, "label": label, "source": token_source}
            for key in list(self.stats_agent_token_state):
                if key not in seen_keys:
                    self.stats_agent_token_state.pop(key, None)
            for key in list(self.stats_agent_activity_state):
                if key not in seen_keys:
                    self.stats_agent_activity_state.pop(key, None)
        active_agents = max(0, len(rows) - inactive_agents)
        record: dict[str, Any] = {
            "time": sample_time,
            "ask_agent_total": ask_agents,
            "run_agent_total": run_agents,
            "transition_agent_total": transition_agents,
            "idle_agent_total": idle_agents,
            "active_agent_total": active_agents,
            "inactive_agent_total": inactive_agents,
            "agent_activity_samples": 1,
        }
        if agent_token_records:
            record["_agent_token_records"] = agent_token_records
        return record

    def current_stats_sample(self) -> tuple[dict[str, Any], bool]:
        now = time.time()
        monotonic_now = time.monotonic()
        with self.stats_sample_lock:
            cached = self.stats_sample_cached_payload
            cached_monotonic = self.stats_sample_cached_monotonic
            use_cached = cached is not None and cached_monotonic is not None and monotonic_now - cached_monotonic < STATS_SAMPLE_CACHE_SECONDS
            if use_cached:
                sample = dict(cached)
                record_cpu_sample = False
            else:
                process_time = time.process_time()
                cpu_percent = 0.0
                if self.stats_sample_last_monotonic is not None and self.stats_sample_last_process_time is not None:
                    elapsed = monotonic_now - self.stats_sample_last_monotonic
                    cpu_elapsed = process_time - self.stats_sample_last_process_time
                    if elapsed > 0 and cpu_elapsed >= 0:
                        cpu_percent = clamp_cpu_percent((cpu_elapsed / elapsed) * 100.0)
                system_cpu_times = current_system_cpu_times()
                if system_cpu_times is not None:
                    system_cpu_percent = system_cpu_percent_from_times(self.stats_sample_last_system_cpu_times, system_cpu_times)
                    self.stats_sample_last_system_cpu_times = system_cpu_times
                else:
                    system_cpu_percent = current_system_cpu_percent_from_ps() or 0.0
                self.stats_sample_last_monotonic = monotonic_now
                self.stats_sample_last_process_time = process_time
                sample = {
                    "time": now,
                    "pid": os.getpid(),
                    "started_at": SERVER_STARTED_AT,
                    "uptime_seconds": max(0.0, now - SERVER_STARTED_AT),
                    "cpu_percent": round(cpu_percent, 3),
                    "system_cpu_percent": round(system_cpu_percent, 3),
                    "rss_bytes": current_process_rss_bytes(),
                }
                self.stats_sample_cached_monotonic = monotonic_now
                self.stats_sample_cached_payload = dict(sample)
                record_cpu_sample = True
        return sample, record_cpu_sample

    def record_stats_global_sample(self, trigger: str = "sampler", token_consumer: bool = False) -> dict[str, Any]:
        started = time.perf_counter()
        shared_enabled = self.stats_history_uses_shared_status()
        if shared_enabled and not self.background_can_run(BACKGROUND_ROLE_STATS_SAMPLER):
            shared = self.merge_shared_stats_history(max_age_seconds=None)
            sample = shared.get("sample") if isinstance(shared.get("sample"), dict) else {}
            self.record_performance_sample(
                BACKGROUND_ROLE_STATS_SAMPLER,
                "global-sample",
                trigger=trigger,
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=sample,
                cache_status="follower",
                record_time=shared.get("updated_at") if shared.get("updated_at") else None,
                details={"shared": bool(shared.get("ok")), "fresh": bool(shared.get("fresh")), "token_consumer": bool(token_consumer)},
            )
            return dict(sample)
        if shared_enabled:
            self.merge_shared_stats_history(max_age_seconds=None)
        sample, record_cpu_sample = self.current_stats_sample()
        include_token_rates = self.stats_agent_token_sampling_due(float(sample["time"]), token_consumer=token_consumer) if record_cpu_sample else False
        agent_record = self.stats_agent_activity_record(sample["time"], include_token_rates=include_token_rates) if record_cpu_sample else None
        agent_token_records = list(agent_record.pop("_agent_token_records", [])) if isinstance(agent_record, dict) else []
        now = float(sample.get("time") or time.time())
        with self.stats_history_lock:
            if record_cpu_sample:
                record = {
                    "time": sample["time"],
                    "cpu_total_percent": sample["cpu_percent"],
                    "cpu_count": 1,
                    "system_cpu_total_percent": sample["system_cpu_percent"],
                    "system_cpu_count": 1,
                }
                if agent_record:
                    record.update(agent_record)
                self.stats_history_merge_record_locked(record, now, fields=STATS_HISTORY_SERVER_FIELDS, client_id=None)
                for token_record in agent_token_records:
                    if isinstance(token_record, dict):
                        self.stats_history_merge_record_locked(token_record, now, fields=STATS_HISTORY_SERVER_FIELDS, client_id=None)
            self.stats_history_compact_locked(now)
        if shared_enabled and record_cpu_sample:
            self.write_shared_stats_history(sample)
        self.record_performance_sample(
            BACKGROUND_ROLE_STATS_SAMPLER,
            "global-sample",
            trigger=trigger,
            compute_ms=(time.perf_counter() - started) * 1000,
            payload=sample,
            cache_status="sampled" if record_cpu_sample else "cached",
            record_time=sample["time"],
            details={"agent_tokens": bool(agent_token_records), "token_consumer": bool(token_consumer), "token_due": bool(include_token_rates)},
        )
        return sample

    def stats_history_sampler_loop(self) -> None:
        try:
            while not self.stats_history_sampler_stop_event.is_set():
                started = time.monotonic()
                try:
                    if self.stats_history_uses_shared_status() and not self.background_can_run(BACKGROUND_ROLE_STATS_SAMPLER):
                        self.merge_shared_stats_history(max_age_seconds=None)
                    else:
                        self.record_stats_global_sample(trigger="sampler")
                except (OSError, RuntimeError, ValueError) as exc:
                    self.log_event(None, "stats_history_error", f"Stats history sample failed: {exc}", {})
                elapsed = max(0.0, time.monotonic() - started)
                if self.stats_history_sampler_stop_event.wait(max(0.1, STATS_HISTORY_SAMPLER_SECONDS - elapsed)):
                    return
        finally:
            with self.stats_history_sampler_lock:
                self.stats_history_sampler_running = False

    def start_stats_history_sampler(self) -> bool:
        with self.stats_history_sampler_lock:
            if self.stats_history_sampler_running:
                thread = self.stats_history_sampler_thread
                if thread is not None and thread.is_alive():
                    return False
            self.stats_history_sampler_stop_event.clear()
            self.stats_history_sampler_running = True
            worker = threading.Thread(target=self.stats_history_sampler_loop, name="stats-history-sampler", daemon=True)
            self.stats_history_sampler_thread = worker
            worker.start()
            return True

    def stop_stats_history_sampler(self) -> None:
        self.stats_history_sampler_stop_event.set()
        with self.stats_history_sampler_lock:
            thread = self.stats_history_sampler_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def stats_sample_payload(self, since: int = 0, client_id: str = "", token_consumer: bool = False) -> dict[str, Any]:
        shared_enabled = self.stats_history_uses_shared_status()
        if shared_enabled and token_consumer:
            consumer_until = time.time() + STATS_AGENT_TOKEN_CONSUMER_TTL_SECONDS
            with self.stats_agent_token_lock:
                self.stats_agent_token_consumer_until = max(self.stats_agent_token_consumer_until, consumer_until)
            self.write_shared_stats_token_consumer_until(consumer_until)
        if shared_enabled and not self.background_can_run(BACKGROUND_ROLE_STATS_SAMPLER):
            shared = self.merge_shared_stats_history(max_age_seconds=STATS_SHARED_FRESH_SECONDS)
            sample = shared.get("sample") if isinstance(shared.get("sample"), dict) else {}
            if not shared.get("fresh"):
                self.record_background_follower_stale_read(BACKGROUND_ROLE_STATS_SAMPLER)
            if not sample:
                sample, _record_cpu_sample = self.current_stats_sample()
            with self.stats_history_lock:
                self.stats_history_compact_locked(float(sample.get("time") or time.time()))
                history = self.stats_history_payload_locked(max(0, since), client_id=client_id)
            return {
                "ok": True,
                **sample,
                "history": history,
                "shared_stats": {
                    "enabled": True,
                    "fresh": bool(shared.get("fresh")),
                    "updated_at": float(shared.get("updated_at") or 0.0),
                    "rev": int(shared.get("rev") or 0),
                    "role": "follower",
                },
            }
        sample = self.record_stats_global_sample(trigger="api", token_consumer=token_consumer)
        with self.stats_history_lock:
            self.stats_history_compact_locked(float(sample.get("time") or time.time()))
            history = self.stats_history_payload_locked(max(0, since), client_id=client_id)
        return {
            "ok": True,
            **sample,
            "history": history,
            "shared_stats": {
                "enabled": shared_enabled,
                "fresh": True,
                "updated_at": float(sample.get("time") or time.time()),
                "rev": self.stats_history_shared_rev,
                "role": "owner" if shared_enabled else "local",
            },
        }

    def start_background_owner(self, port: int | None = None) -> bool:
        self.background_owner = BackgroundOwnerRegistry(
            control_socket=str(self.control_server.path),
            port=port,
            project_root=str(PROJECT_ROOT),
            on_demote=self.demote_background_owner,
            on_acquire=self.handle_background_owner_acquired,
        )
        file_index.set_background_owner_checker(self.background_can_run)
        acquired = self.background_owner.start()
        self.merge_shared_stats_history(max_age_seconds=None)
        if not acquired and self.background_owner.status == "blocked_by_unreachable_owner":
            self.log_event(None, "background_owner_blocked", "Background owner takeover blocked", self.background_owner.status_payload())
        return acquired

    def handle_background_owner_acquired(self, status: dict[str, Any]) -> None:
        transition = str(status.get("last_transition") or "acquired")
        if transition == "takeover":
            self.log_event(None, "background_owner_takeover", "Background owner moved to this server", status.get("last_transition_details", {}))
        else:
            self.log_event(None, "background_owner_acquired", "Background owner acquired by this server", status.get("generation", {}))
        self.warm_start_session_files_payload_cache()
        self.warm_start_tabber_activity_cache()
        self.merge_shared_stats_history(max_age_seconds=None)
        self.publish_background_client_event("background_owner_changed", self.background_owner.status_payload(), trigger="background-owner", cache="ready")

    def background_can_run(self, role: str) -> bool:
        return self.background_owner.can_run(role)

    def background_owner_status_payload(self) -> tuple[dict[str, Any], HTTPStatus]:
        payload = self.background_owner.status_payload()
        payload["perf"] = self.performance_metrics_payload()
        return payload, HTTPStatus.OK

    def background_owner_claim_payload(self) -> tuple[dict[str, Any], HTTPStatus]:
        was_owner = self.background_owner.is_owner()
        ok = self.background_owner.attempt_takeover()
        status_payload, _status = self.background_owner_status_payload()
        payload = {
            "ok": bool(ok),
            "claimed": bool(ok and not was_owner),
            "was_owner": bool(was_owner),
            "status": status_payload,
        }
        if not ok:
            payload["error"] = str(status_payload.get("last_error") or "background owner takeover failed")
            return payload, HTTPStatus.CONFLICT
        return payload, HTTPStatus.OK

    def demote_background_owner(self) -> None:
        with self.tabber_activity_cache_lock:
            self.tabber_activity_cache_warmer_running = False
            self.tabber_activity_cache_refreshing = False
        with self.session_files_cache_lock:
            self.session_files_refreshing_cache_keys.clear()
        file_index.clear_memory_indexes()
        self.publish_client_event("background_owner_changed", self.background_owner.status_payload(), trigger="background-owner", cache="ready")

    def background_release_owner(self, requester: dict[str, Any]) -> dict[str, Any]:
        was_owner = self.background_owner.is_owner()
        self.background_owner.release_owner("control_release")
        if was_owner:
            self.log_event(None, "background_owner_released", "Background owner released for another server", {"requester": requester})
        return {"ok": True, "owner": False, "status": self.background_owner.status_payload()}

    def background_refresh_should_fallback(self, result: dict[str, Any]) -> bool:
        return bool(result.get("fallback"))

    def record_background_avoided_recompute(self, role: str) -> None:
        recorder = getattr(self.background_owner, "record_avoided_recompute", None)
        if callable(recorder):
            recorder(role)

    def record_background_follower_stale_read(self, role: str) -> None:
        recorder = getattr(self.background_owner, "record_follower_stale_read", None)
        if callable(recorder):
            recorder(role)

    def record_background_search_index_bytes_written(self, byte_count: int) -> None:
        recorder = getattr(self.background_owner, "record_search_index_bytes_written", None)
        if callable(recorder):
            recorder(byte_count)
        self.record_performance_sample(
            BACKGROUND_ROLE_SEARCH_INDEX,
            "bytes-written",
            trigger="search-index",
            payload_bytes=max(0, int(byte_count)),
            cache_status="write",
        )

    def record_background_fallback(self, role: str, result: dict[str, Any], payload: dict[str, Any] | None = None) -> None:
        recorder = getattr(self.background_owner, "record_fallback", None)
        if callable(recorder):
            recorder(role)
        self.log_event(None, "background_refresh_fallback", "Background owner refresh fallback engaged", {"role": role, "result": result, "payload": payload or {}})

    def request_background_refresh(self, role: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        request_payload = payload or {}
        if hasattr(self.background_owner, "request_owner_refresh"):
            result = self.background_owner.request_owner_refresh(role, request_payload)
        else:
            self.background_owner.record_refresh_request(role)
            result = {"ok": False, "accepted": False, "role": role, "fallback": False}
        cache_status = "coalesced" if result.get("coalesced") else ("fallback" if self.background_refresh_should_fallback(result) else ("accepted" if result.get("accepted") else "rejected"))
        self.record_performance_sample(
            role,
            "background-refresh-request",
            trigger=str(request_payload.get("reason") or result.get("role") or ""),
            compute_ms=(time.perf_counter() - started) * 1000,
            payload=request_payload,
            cache_key=request_payload.get("cache_key", role),
            cache_status=cache_status,
            owner_role="owner" if result.get("local_owner") else "follower",
            details={"accepted": bool(result.get("accepted")), "fallback": bool(result.get("fallback")), "coalesced": bool(result.get("coalesced"))},
        )
        if result.get("local_owner"):
            if not result.get("coalesced"):
                self.log_sampled_background_refresh_event(
                    "background_refresh_started",
                    role,
                    "Background refresh accepted by local owner",
                    self.background_refresh_event_details(role, request_payload, extra={"source": "owner-request"}),
                )
        elif self.background_refresh_should_fallback(result):
            self.record_background_fallback(role, result, payload)
        if result.get("coalesced"):
            return result
        return result

    def refresh_sessions(self, maintenance: bool = True) -> list[str]:
        sessions, error = list_tmux_session_names()
        if error is None:
            self.sessions = sessions
            if not maintenance:
                return []
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

    def shared_background_client_event_record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "id": uuid.uuid4().hex,
            "time": time.time(),
            "type": event_type,
            "payload": dict(payload),
            "source": self.background_owner.owner_payload(),
        }

    def write_shared_background_client_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.shared_background_client_event_record(event_type, payload)
        with file_lock(BACKGROUND_CLIENT_EVENTS_PATH, dir_mode=0o700):
            try:
                manifest = json.loads(BACKGROUND_CLIENT_EVENTS_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                manifest = {}
            raw_events = manifest.get("events") if isinstance(manifest, dict) else []
            events = [item for item in raw_events if isinstance(item, dict)] if isinstance(raw_events, list) else []
            events.append(record)
            events = events[-BACKGROUND_CLIENT_EVENT_MANIFEST_LIMIT:]
            payload_text = json.dumps({"version": 1, "events": events}, sort_keys=True, separators=(",", ":")) + "\n"
            atomic_write_text(BACKGROUND_CLIENT_EVENTS_PATH, payload_text, mode=0o600)
        return record

    def notify_background_client_event_followers(self, event_type: str, payload: dict[str, Any], shared_event: dict[str, Any]) -> None:
        source = self.background_owner.owner_payload()
        source_generation = str(source.get("generation_id") or "")
        request = {
            "action": "background_client_event",
            "event_type": event_type,
            "payload": payload,
            "shared_event": shared_event,
            "requester": source,
        }
        for record in self.background_owner.live_generation_records():
            if str(record.get("generation_id") or "") == source_generation:
                continue
            if not str(record.get("control_socket") or ""):
                continue
            send_yolomux_control_request(record, request, timeout=BACKGROUND_CLIENT_EVENT_NOTIFY_TIMEOUT_SECONDS)

    def publish_background_client_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        trigger: str = "background-refresh",
        cache: str | None = "ready",
    ) -> dict[str, Any]:
        if event_type not in BACKGROUND_CLIENT_EVENT_TYPES or event_type not in CLIENT_EVENT_TYPES:
            return self.publish_client_event(event_type, payload, trigger=trigger, cache=cache)
        event = self.publish_client_event(event_type, payload, trigger=trigger, cache=cache)
        event_payload = event.get("payload") if isinstance(event, dict) else {}
        shared_event = self.write_shared_background_client_event(event_type, event_payload if isinstance(event_payload, dict) else {})
        self.notify_background_client_event_followers(event_type, event_payload if isinstance(event_payload, dict) else {}, shared_event)
        return event

    def publish_background_refresh_done(self, role: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event_payload = {"role": role}
        event_payload.update(payload or {})
        self.record_performance_sample(
            role,
            "background-refresh-done",
            trigger=str(event_payload.get("trigger") or "background-refresh"),
            compute_ms=self.float_value(event_payload.get("compute_ms"), 0.0),
            payload=event_payload,
            cache_key=event_payload.get("cache_key", role),
            cache_status=str(event_payload.get("cache") or "ready"),
        )
        return self.publish_background_client_event("background_refresh_done", event_payload, trigger="background-refresh", cache="ready")

    def handle_background_client_event(self, request: dict[str, Any]) -> dict[str, Any]:
        event_type = str(request.get("event_type") or "")
        if event_type not in BACKGROUND_CLIENT_EVENT_TYPES or event_type not in CLIENT_EVENT_TYPES:
            return {"ok": False, "error": f"unsupported background client event: {event_type}"}
        if event_type == "attention_acks_changed":
            if not self.merge_shared_attention_acks():
                return {"ok": True, "accepted": True, "noop": True}
            self.invalidate_auto_approve_cache()
            self.publish_client_event(
                "auto_approve_changed",
                {"refresh": True, "trigger": "attention_ack_sync"},
                trigger="background-fanout",
                cache="ready",
            )
            return {"ok": True, "accepted": True, "event": {"type": event_type}}
        raw_payload = request.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        event = self.publish_client_event(event_type, payload, trigger="background-fanout", cache="ready")
        return {"ok": True, "accepted": True, "event": {"id": event.get("id"), "type": event_type}}

    def client_event_payload_signature(self, payload: Any) -> str:
        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            return str(payload)

    def stable_signature_payload(
        self,
        payload: Any,
        volatile_keys: frozenset[str] = CLIENT_EVENT_SIGNATURE_VOLATILE_KEYS,
    ) -> Any:
        if isinstance(payload, dict):
            return {
                key: self.stable_signature_payload(value, volatile_keys)
                for key, value in payload.items()
                if key not in volatile_keys
            }
        if isinstance(payload, list):
            return [self.stable_signature_payload(item, volatile_keys) for item in payload]
        return payload

    def stable_client_event_signature_payload(self, payload: Any) -> Any:
        return self.stable_signature_payload(payload)

    def stable_client_event_payload_signature(self, payload: Any) -> str:
        return self.client_event_payload_signature(self.stable_client_event_signature_payload(payload))

    def transcripts_payload_event_signature(self, payload: dict[str, Any]) -> str:
        return self.stable_client_event_payload_signature(payload)

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

    def jittered_interactive_event_poll_seconds(self, base_seconds: float) -> float:
        jitter = min(SERVER_INTERACTIVE_EVENT_POLL_JITTER_SECONDS, max(0.0, base_seconds * 0.25))
        if jitter <= 0:
            return max(0.25, base_seconds)
        return max(0.25, base_seconds + random.uniform(-jitter, jitter))

    def server_auto_approve_event_poll_seconds(self) -> float:
        return self.jittered_interactive_event_poll_seconds(SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS)

    def server_attention_ack_event_poll_seconds(self) -> float:
        return self.server_auto_approve_event_poll_seconds()

    def server_tmux_signal_event_poll_seconds(self) -> float:
        return self.jittered_interactive_event_poll_seconds(SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS)

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
        return self.stable_signature_payload(payload)

    def recent_tmux_signal_removal_event(self, generated_at: Any = None) -> dict[str, Any]:
        with self.client_watch_lock:
            event = dict(self.client_watch_tmux_signal_removal_event)
        event_time = float(event.get("time") or 0.0)
        if event_time <= 0:
            return {}
        reference_time = float(generated_at or 0.0)
        if reference_time <= 0:
            reference_time = time.time()
        if abs(reference_time - event_time) > TMUX_SIGNAL_REMOVAL_EVENT_TTL_SECONDS:
            return {}
        return event

    def add_tmux_signal_removal_event_fields(self, payload: dict[str, Any], removed_keys: list[str]) -> dict[str, Any]:
        if not removed_keys:
            return payload
        next_payload = dict(payload)
        next_payload["removed_window_keys"] = removed_keys
        removal_event = self.recent_tmux_signal_removal_event(next_payload.get("generated_at"))
        if removal_event:
            next_payload["removed_window_event_at"] = removal_event.get("time")
            next_payload["removed_window_event_type"] = removal_event.get("type")
        return next_payload

    def tmux_signal_patch_payload(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
        previous_windows = previous.get("windows") if isinstance(previous, dict) else None
        current_windows = current.get("windows") if isinstance(current, dict) else None
        if not isinstance(previous_windows, list) or not isinstance(current_windows, list):
            return {"data": current}
        previous_by_key = {
            key: window
            for window in previous_windows
            if isinstance(window, dict) and (key := window_record_key(window))
        }
        changed_windows: list[dict[str, Any]] = []
        current_keys: set[str] = set()
        for window in current_windows:
            if not isinstance(window, dict):
                continue
            key = window_record_key(window)
            if not key:
                return {"data": current}
            current_keys.add(key)
            if self.stable_client_event_payload_signature(previous_by_key.get(key)) != self.stable_client_event_payload_signature(window):
                changed_windows.append(window)
        removed_keys = sorted(set(previous_by_key) - current_keys)
        patchable_window_meta_keys = {"windows", "window_count"}
        previous_meta = {key: value for key, value in self.tmux_signal_signature_payload(previous or {}).items() if key not in patchable_window_meta_keys}
        current_meta = {key: value for key, value in self.tmux_signal_signature_payload(current).items() if key not in patchable_window_meta_keys}
        if previous_meta != current_meta:
            return {"data": self.add_tmux_signal_removal_event_fields(current, removed_keys)}
        patch = {
            "patch": True,
            "windows": changed_windows,
            "removed_window_keys": removed_keys,
            "window_count": current.get("window_count", len(current_windows)),
            "ok": current.get("ok", True),
            "generated_at": current.get("generated_at"),
            "compute_ms": current.get("compute_ms"),
        }
        return self.add_tmux_signal_removal_event_fields(patch, removed_keys)

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

    def mark_tabber_activity_consumer(self, visible: bool = True) -> bool:
        if not visible:
            return False
        until = time.monotonic() + max(TABBER_ACTIVITY_CONSUMER_TTL_SECONDS, self.tabber_activity_refresh_seconds() * 2.0)
        with self.tabber_activity_cache_lock:
            self.tabber_activity_consumer_until = max(self.tabber_activity_consumer_until, until)
        return True

    def tabber_activity_has_recent_consumer(self) -> bool:
        now = time.monotonic()
        with self.tabber_activity_cache_lock:
            return self.tabber_activity_consumer_until > now

    def tabber_activity_idle_refresh_seconds(self) -> float:
        return max(TABBER_ACTIVITY_IDLE_REFRESH_SECONDS, self.tabber_activity_refresh_seconds() * 4.0)

    def client_event_watch_sleep_seconds(self, now: float) -> float:
        next_due = min(
            self.client_event_next_signature_poll_at,
            self.client_event_next_file_poll_at,
            self.client_event_next_background_file_poll_at,
            self.client_event_next_auto_poll_at,
            self.client_event_next_attention_ack_poll_at,
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
        watch_update_started = time.perf_counter()
        self.watch_root_index.update_client_roots(unique)
        self.record_performance_sample(
            BACKGROUND_ROLE_WATCH_ROOTS,
            "client-roots-update",
            trigger="watch-roots-api",
            compute_ms=(time.perf_counter() - watch_update_started) * 1000,
            payload={"roots": unique, "files": unique_files, "background_files": unique_background_files},
            cache_status="updated",
            count=len(unique),
        )
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
                rows.append((name, agent.kind or "", agent.session_id or "", transcript))
        return tuple(rows)

    def transcript_content_watch_signature(self, sessions: dict[str, SessionInfo]) -> tuple[Any, ...]:
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
        started = time.perf_counter()
        roots = self.filesystem_roots_for_watch(sessions)
        signature = tuple((root, filesystem_watch_signature(root)) for root in roots)
        self.record_performance_sample(
            BACKGROUND_ROLE_WATCH_ROOTS,
            "watch-signature",
            trigger="poll",
            compute_ms=(time.perf_counter() - started) * 1000,
            payload={"roots": roots, "signature_count": len(signature)},
            cache_status="computed",
            count=len(roots),
        )
        return signature

    def request_watch_roots_owner_refresh(self, roots: list[str], reason: str) -> None:
        if not roots:
            return
        self.record_background_avoided_recompute(BACKGROUND_ROLE_WATCH_ROOTS)
        self.request_background_refresh(BACKGROUND_ROLE_WATCH_ROOTS, {"reason": reason, "roots": roots[:CLIENT_WATCH_ROOT_LIMIT]})

    def follower_filesystem_roots_watch_signature(self, sessions: dict[str, SessionInfo]) -> tuple[Any, ...]:
        roots = self.filesystem_roots_for_watch(sessions)
        self.request_watch_roots_owner_refresh(roots, "poll")
        with self.client_watch_lock:
            return self.client_watch_filesystem_signature or (("watch-roots", "follower"),)

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

    def record_filesystem_watch_snapshot(self, signature: tuple[Any, ...]) -> str:
        now = time.time()
        with self.client_watch_lock:
            if self.client_watch_filesystem_history and self.client_watch_filesystem_history[-1]["signature"] == signature:
                return str(self.client_watch_filesystem_history[-1]["token"])
            signature_text = self.client_event_payload_signature(signature)
            digest = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:16]
            token = f"{int(now * 1000)}-{digest}"
            self.client_watch_filesystem_history.append({
                "token": token,
                "created_at": now,
                "signature": copy.deepcopy(signature),
            })
            min_created_at = now - FILESYSTEM_WATCH_HISTORY_SECONDS
            self.client_watch_filesystem_history = [
                record
                for record in self.client_watch_filesystem_history[-FILESYSTEM_WATCH_HISTORY_LIMIT:]
                if float(record.get("created_at") or 0.0) >= min_created_at
            ]
            return token

    def filesystem_watch_record_for_token(self, token: str) -> dict[str, Any] | None:
        clean_token = str(token or "").strip()
        if not clean_token:
            return None
        with self.client_watch_lock:
            for record in self.client_watch_filesystem_history:
                if record.get("token") == clean_token:
                    return copy.deepcopy(record)
        return None

    def latest_filesystem_watch_record(self, refresh: bool = False) -> dict[str, Any] | None:
        with self.client_watch_lock:
            if self.client_watch_filesystem_history and not refresh:
                return copy.deepcopy(self.client_watch_filesystem_history[-1])
        sessions, _errors = discover_sessions(self.sessions)
        signature = self.filesystem_roots_watch_signature(sessions)
        if not signature:
            return None
        token = self.record_filesystem_watch_snapshot(signature)
        return self.filesystem_watch_record_for_token(token)

    def filesystem_watch_signature_for_roots(self, roots: list[str]) -> tuple[Any, ...]:
        return tuple((root, filesystem_watch_signature(root)) for root in roots[:CLIENT_WATCH_ROOT_LIMIT])

    def filesystem_watch_full_due(self) -> bool:
        with self.client_watch_lock:
            return self.client_watch_filesystem_last_full_at <= 0.0 or time.monotonic() - self.client_watch_filesystem_last_full_at >= FILESYSTEM_WATCH_KEYFRAME_SECONDS

    def mark_filesystem_watch_full_sent(self) -> None:
        with self.client_watch_lock:
            self.client_watch_filesystem_last_full_at = time.monotonic()

    def filesystem_watch_full_payload(self, record: dict[str, Any], reason: str = "full") -> dict[str, Any]:
        signature = record.get("signature")
        roots = sorted(filesystem_signature_root_map(signature).keys())
        payload = self.filesystem_push_payload(roots)
        payload["mode"] = "full"
        payload["reason"] = reason
        payload["token"] = record.get("token", "")
        return payload

    def filesystem_watch_diff_payload(self, since_token: str = "", force_full: bool = False) -> dict[str, Any]:
        current = self.latest_filesystem_watch_record(refresh=force_full)
        if current is None:
            return {"mode": "none", "token": "", "directories": [], "removed_roots": []}
        if force_full:
            return self.filesystem_watch_full_payload(current, "forced")
        previous = self.filesystem_watch_record_for_token(since_token)
        if previous is None:
            return self.filesystem_watch_full_payload(current, "stale-since")
        current_signature = current.get("signature")
        previous_signature = previous.get("signature")
        if previous.get("token") == current.get("token") or previous_signature == current_signature:
            return {
                "mode": "none",
                "token": current.get("token", ""),
                "since": previous.get("token", ""),
                "directories": [],
                "removed_roots": [],
                "change_summary": filesystem_change_summary(previous_signature, current_signature),
            }
        changed_roots, removed_roots = filesystem_changed_roots(previous_signature, current_signature)
        payload = self.filesystem_push_payload(changed_roots)
        payload["mode"] = "diff"
        payload["token"] = current.get("token", "")
        payload["since"] = previous.get("token", "")
        payload["removed_roots"] = removed_roots
        payload["change_summary"] = filesystem_change_summary(previous_signature, current_signature)
        return payload

    def publish_filesystem_ready_event(
        self,
        roots: list[str],
        trigger: str = "watch",
        change_summary: dict[str, Any] | None = None,
        current_signature: tuple[Any, ...] | None = None,
        force_full: bool = False,
    ) -> list[str]:
        if not roots:
            return []
        started = time.perf_counter()
        filesystem_signature = current_signature or self.filesystem_watch_signature_for_roots(roots)
        token = self.record_filesystem_watch_snapshot(filesystem_signature)
        with self.client_watch_lock:
            previous_signature = self.client_watch_filesystem_payload_signature
            self.client_watch_filesystem_payload_signature = token
        if previous_signature == token:
            return []
        full = force_full or trigger != "watch" or self.filesystem_watch_full_due()
        if full:
            payload = self.filesystem_push_payload(roots)
            payload["mode"] = "full"
            payload["token"] = token
            self.mark_filesystem_watch_full_sent()
        else:
            payload = {
                "roots": roots,
                "mode": "diff",
                "refresh": True,
                "token": token,
                "change_summary": change_summary or {},
                "compute_ms": round((time.perf_counter() - started) * 1000, 1),
            }
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

    def clear_transcript_content_caches(self) -> None:
        with self.transcript_tail_cache_lock:
            self.transcript_tail_cache.clear()
        with self.context_items_cache_lock:
            self.context_items_cache.clear()

    def clear_transcript_caches(self) -> None:
        self.clear_transcript_content_caches()
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
            signature = self.transcripts_payload_event_signature(payload)
            with self.client_watch_lock:
                previous_signature = self.client_watch_transcripts_payload_signature
                self.client_watch_transcripts_payload_signature = signature
            if previous_signature != signature:
                self.publish_client_event(
                    "transcripts_changed",
                    {"signature": signature, "refresh": True},
                    trigger="watch_state",
                    cache="ready",
                    compute_ms=(time.perf_counter() - started) * 1000,
                )
            self.publish_context_items_ready_events(trigger="watch_state")
            self.publish_activity_summary_ready_events(trigger="watch_state")
            roots = self.client_watch_roots_snapshot()
            if self.background_can_run(BACKGROUND_ROLE_WATCH_ROOTS):
                self.publish_filesystem_ready_event(roots, trigger="watch_state")
            else:
                self.request_watch_roots_owner_refresh(roots, "watch_state")
            self.publish_session_files_ready_events(trigger="watch_state")
        finally:
            with self.client_watch_lock:
                self.client_watch_snapshot_running = False

    def poll_client_events_once(self) -> list[str]:
        sessions, _errors = discover_sessions(self.sessions)
        settings_signature = self.settings_watch_signature()
        transcripts_signature = self.transcripts_watch_signature(sessions)
        transcript_content_signature = self.transcript_content_watch_signature(sessions)
        if self.background_can_run(BACKGROUND_ROLE_WATCH_ROOTS):
            filesystem_signature = self.filesystem_roots_watch_signature(sessions)
        else:
            filesystem_signature = self.follower_filesystem_roots_watch_signature(sessions)
        events: list[str] = []
        with self.client_watch_lock:
            initialized = self.client_watch_initialized
            previous_filesystem_signature = self.client_watch_filesystem_signature
            settings_changed = initialized and self.client_watch_settings_signature != settings_signature
            transcripts_changed = initialized and self.client_watch_transcripts_signature != transcripts_signature
            transcript_content_changed = initialized and self.client_watch_transcript_content_signature != transcript_content_signature
            filesystem_changed = initialized and previous_filesystem_signature != filesystem_signature
            self.client_watch_initialized = True
            self.client_watch_settings_signature = settings_signature
            self.client_watch_transcripts_signature = transcripts_signature
            self.client_watch_transcript_content_signature = transcript_content_signature
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
            self.publish_client_event(
                "transcripts_changed",
                {"signature": transcripts_signature, "refresh": True},
                cache="refresh",
            )
            events.append("transcripts_changed")
            events.extend(self.publish_context_items_ready_events(trigger="transcripts_changed"))
            events.extend(self.publish_activity_summary_ready_events(trigger="transcripts_changed"))
            events.extend(self.publish_session_files_ready_events(trigger="transcripts_changed"))
        elif transcript_content_changed:
            self.clear_transcript_content_caches()
            events.extend(self.publish_context_items_ready_events(trigger="transcript_content_changed"))
            events.extend(self.publish_activity_summary_ready_events(trigger="transcript_content_changed"))
            events.extend(self.publish_session_files_ready_events(trigger="transcript_content_changed"))
        if filesystem_changed:
            roots = self.filesystem_roots_for_watch(sessions)
            change_summary = filesystem_change_summary(previous_filesystem_signature, filesystem_signature)
            events.extend(self.publish_filesystem_ready_event(roots, change_summary=change_summary, current_signature=filesystem_signature))
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
        signature = self.stable_client_event_payload_signature(payload)
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
        signature_payload = {"status": int(status), "data": payload}
        serialization_started = time.perf_counter()
        signature = self.stable_client_event_payload_signature(signature_payload)
        timings = copy.deepcopy(payload.get("timings")) if isinstance(payload, dict) and isinstance(payload.get("timings"), dict) else {}
        add_phase_timing(timings, "serialization", serialization_started)
        with self.client_watch_lock:
            previous = self.client_watch_auto_approve_signature
            self.client_watch_auto_approve_signature = signature
        if not previous:
            return []
        if previous == signature:
            return []
        event_payload: dict[str, Any] = {"status": int(status), "refresh": True, "signature": signature}
        if timings:
            event_payload["timings"] = timings
        if isinstance(payload, dict) and isinstance(payload.get("cache"), dict):
            event_payload["cache"] = copy.deepcopy(payload["cache"])
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
        signature = self.stable_client_event_payload_signature(self.tmux_signal_signature_payload(payload))
        with self.client_watch_lock:
            previous = self.client_watch_tmux_signal_signature
            previous_payload = copy.deepcopy(self.client_watch_tmux_signal_payload) if self.client_watch_tmux_signal_payload is not None else None
            self.client_watch_tmux_signal_signature = signature
            self.client_watch_tmux_signal_payload = copy.deepcopy(payload)
        if not previous:
            return []
        if previous == signature:
            return []
        event_payload = self.tmux_signal_patch_payload(previous_payload, payload)
        self.publish_client_event(
            "tmux_signals_changed",
            event_payload,
            trigger="timer",
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["tmux_signals_changed"]

    def handle_tmux_signal_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or event.get("event") or "")
        if event_type in {"pane-exited", "pane-died", "window-close", "sessions-changed"}:
            event_time = float(event.get("time") or time.time())
            with self.client_watch_lock:
                self.client_watch_tmux_signal_removal_event = {"type": event_type, "time": event_time}
        self.tmux_signal_cache.clear()
        with self.client_watch_lock:
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
        if not previous:
            return []
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
        now = time.monotonic()
        with self.client_watch_lock:
            if self.client_watch_running:
                return
            self.client_watch_stop_event.clear()
            self.client_event_next_auto_poll_at = max(self.client_event_next_auto_poll_at, now + self.server_auto_approve_event_poll_seconds())
            self.client_event_next_attention_ack_poll_at = max(self.client_event_next_attention_ack_poll_at, now + self.server_attention_ack_event_poll_seconds())
            self.client_event_next_tmux_signal_poll_at = max(self.client_event_next_tmux_signal_poll_at, now + self.server_tmux_signal_event_poll_seconds())
            self.client_watch_running = True
        self.start_tmux_signal_event_watcher()
        worker = threading.Thread(target=self.client_event_watch_loop, name="client-event-watch", daemon=True)
        self.client_watch_thread = worker
        worker.start()

    def stop_client_event_watcher(self) -> None:
        self.stop_tmux_signal_event_watcher()
        self.client_watch_stop_event.set()
        self.client_watch_wake_event.set()
        thread = self.client_watch_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self.client_watch_lock:
            if self.client_watch_thread is thread:
                self.client_watch_thread = None
            self.client_watch_running = False

    def stop_client_event_watcher_if_idle(self) -> bool:
        with self.client_events.lock:
            if self.client_events.subscribers:
                return False
        self.stop_client_event_watcher()
        return True

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
        try:
            while not self.client_watch_stop_event.is_set():
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
                    if now >= self.client_event_next_attention_ack_poll_at:
                        self.poll_attention_acks_client_event_once()
                        self.client_event_next_attention_ack_poll_at = now + self.server_attention_ack_event_poll_seconds()
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
        finally:
            with self.client_watch_lock:
                if self.client_watch_thread is threading.current_thread():
                    self.client_watch_thread = None
                self.client_watch_running = False

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
            SESSION_FILES_CACHE_KEY_VERSION,
            session or "",
            session_files.bounded_session_files_hours(hours),
            str(from_ref or ""),
            str(to_ref or ""),
            repo_refs_cache_signature(repo_refs),
            tuple((name, session_info_cache_signature(info)) for name, info in sorted(infos.items())),
        )

    def session_files_disk_cache_path(self, key: tuple[Any, ...]) -> tuple[Path, str]:
        key_text = self.client_event_payload_signature(key)
        signature = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        return SESSION_FILES_CACHE_DIR / f"{signature}.json", signature

    def session_files_disk_manifest_path(self, signature: str) -> Path:
        return SESSION_FILES_CACHE_DIR / f"{signature}.manifest.json"

    def session_files_disk_cache_entries(self) -> list[dict[str, Any]]:
        try:
            paths = sorted(SESSION_FILES_CACHE_DIR.glob("*.json"))
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for path in paths:
            if path.name.endswith(".manifest.json"):
                continue
            signature = path.stem
            manifest_path = self.session_files_disk_manifest_path(signature)
            try:
                payload_stat = path.stat()
            except OSError:
                continue
            size = int(payload_stat.st_size)
            mtime = float(payload_stat.st_mtime)
            try:
                manifest_stat = manifest_path.stat()
                size += int(manifest_stat.st_size)
                mtime = max(mtime, float(manifest_stat.st_mtime))
            except OSError:
                pass
            entries.append({"path": path, "manifest_path": manifest_path, "signature": signature, "size": size, "mtime": mtime})
        return entries

    def remove_session_files_disk_cache_entry(self, entry: dict[str, Any]) -> tuple[int, int]:
        removed_files = 0
        removed_bytes = max(0, int(self.float_value(entry.get("size"), 0.0)))
        for path in (entry.get("path"), entry.get("manifest_path")):
            if not isinstance(path, Path):
                continue
            try:
                path.unlink()
                removed_files += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.debug("failed to remove session-files cache entry %s: %s", path, exc)
        return removed_files, removed_bytes

    def prune_session_files_disk_cache(
        self,
        *,
        max_age_seconds: float | None = None,
        max_bytes: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        max_age = SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS if max_age_seconds is None else max(0.0, float(max_age_seconds))
        byte_cap = SESSION_FILES_DISK_CACHE_MAX_BYTES if max_bytes is None else max(0, int(max_bytes))
        current_time = time.time() if now is None else float(now)
        entries = self.session_files_disk_cache_entries()
        kept: list[dict[str, Any]] = []
        to_remove: list[dict[str, Any]] = []
        for entry in entries:
            age_seconds = max(0.0, current_time - float(entry.get("mtime") or 0.0))
            if max_age and age_seconds > max_age:
                to_remove.append(entry)
            else:
                kept.append(entry)
        total_bytes = sum(max(0, int(self.float_value(entry.get("size"), 0.0))) for entry in kept)
        if byte_cap >= 0 and total_bytes > byte_cap:
            for entry in sorted(kept, key=lambda item: (float(item.get("mtime") or 0.0), str(item.get("path") or ""))):
                if total_bytes <= byte_cap:
                    break
                to_remove.append(entry)
                total_bytes -= max(0, int(self.float_value(entry.get("size"), 0.0)))
        removed_files = 0
        removed_bytes = 0
        seen_paths: set[Path] = set()
        for entry in to_remove:
            path = entry.get("path")
            if isinstance(path, Path):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
            files, bytes_removed = self.remove_session_files_disk_cache_entry(entry)
            removed_files += files
            removed_bytes += bytes_removed
        return {
            "entries": len(entries),
            "removed_entries": len(seen_paths),
            "removed_files": removed_files,
            "removed_bytes": removed_bytes,
            "kept_bytes": max(0, total_bytes),
            "max_age_seconds": max_age,
            "max_bytes": byte_cap,
        }

    def run_session_files_disk_cache_prune(self) -> None:
        try:
            result = self.prune_session_files_disk_cache()
        except (OSError, RuntimeError, ValueError) as exc:
            result = {"error": str(exc)}
            logger.warning("session-files disk cache prune failed: %s", exc)
        with self.session_files_disk_prune_lock:
            self.session_files_disk_prune_last_result = result
            self.session_files_disk_prune_running = False
        if result.get("removed_entries"):
            self.log_event(None, "session_files_cache_pruned", "Session-files disk cache pruned", result)

    def request_session_files_disk_cache_prune(self, reason: str = "") -> bool:
        now = time.monotonic()
        with self.session_files_disk_prune_lock:
            if self.session_files_disk_prune_running or now < self.session_files_disk_prune_next_at:
                return False
            self.session_files_disk_prune_running = True
            self.session_files_disk_prune_next_at = now + SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS
        worker = threading.Thread(target=self.run_session_files_disk_cache_prune, name="session-files-cache-prune", daemon=True)
        worker.start()
        return True

    def session_files_payload_signature(self, payload: SessionFilesPayload | dict[str, Any]) -> str:
        payload_text = self.client_event_payload_signature(payload)
        return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    def set_session_files_memory_cache(
        self,
        key: tuple[Any, ...],
        payload: SessionFilesPayload,
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
    ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
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
        payload_signature = str(record.get("payload_signature") or self.session_files_payload_signature(payload))
        manifest_path = self.session_files_disk_manifest_path(signature)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            manifest = None
        if isinstance(manifest, dict) and manifest.get("version") == SESSION_FILES_CACHE_VERSION and manifest.get("signature") == signature and manifest.get("payload_signature") == payload_signature:
            try:
                status = HTTPStatus(int(manifest.get("status", int(status))))
                stored_at_wall = float(manifest.get("stored_at", stored_at_wall))
            except (TypeError, ValueError):
                pass
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
        payload: SessionFilesPayload,
        status: HTTPStatus,
    ) -> None:
        payload_signature = self.session_files_payload_signature(payload)
        payload_changed = True
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            existing = None
        if isinstance(existing, dict) and existing.get("version") == SESSION_FILES_CACHE_VERSION and existing.get("signature") == signature:
            existing_payload = existing.get("payload")
            existing_payload_signature = str(existing.get("payload_signature") or "")
            if not existing_payload_signature and isinstance(existing_payload, dict):
                existing_payload_signature = self.session_files_payload_signature(existing_payload)
            try:
                existing_status = int(existing.get("status", int(status)))
            except (TypeError, ValueError):
                existing_status = -1
            payload_changed = existing_payload_signature != payload_signature or existing_status != int(status)
        stored_at = time.time()
        record = {
            "version": SESSION_FILES_CACHE_VERSION,
            "signature": signature,
            "stored_at": stored_at,
            "status": int(status),
            "payload_signature": payload_signature,
            "payload": payload,
        }
        if payload_changed:
            atomic_write_text(path, json.dumps(record, sort_keys=True, separators=(",", ":")), mode=0o600)
        manifest = {
            "version": SESSION_FILES_CACHE_VERSION,
            "signature": signature,
            "stored_at": stored_at,
            "status": int(status),
            "payload_signature": payload_signature,
            "payload_changed": payload_changed,
            "owner": self.background_owner.status_payload().get("generation", {}),
            "refresh_status": "ready",
            "last_error": "",
        }
        atomic_write_text(self.session_files_disk_manifest_path(signature), json.dumps(manifest, sort_keys=True, separators=(",", ":")), mode=0o600)
        self.request_session_files_disk_cache_prune("write")

    def write_session_files_disk_cache(self, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
        path, signature = self.session_files_disk_cache_path(key)
        try:
            with file_lock(path, dir_mode=0o700):
                self.write_session_files_disk_cache_unlocked(path, signature, payload, status)
        except OSError as exc:
            logger.warning("failed to write session-files cache %s: %s", path, exc)

    def compute_session_files_cache_entry(
        self,
        key: tuple[Any, ...],
        compute: Callable[[], tuple[SessionFilesPayload, HTTPStatus]],
    ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float]:
        started = time.perf_counter()
        path, signature = self.session_files_disk_cache_path(key)
        try:
            with file_lock(path, dir_mode=0o700):
                cached = self.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=False)
                if cached:
                    payload, status, _fresh, age_seconds = cached
                    self.record_performance_sample(
                        BACKGROUND_ROLE_SESSION_FILES,
                        "cache-entry",
                        trigger="compute",
                        compute_ms=(time.perf_counter() - started) * 1000,
                        payload=payload,
                        cache_key=key,
                        cache_status="hit:fresh",
                        cache_hit=True,
                        cache_fresh=True,
                    )
                    return payload, status, True, age_seconds
                payload, status = compute()
                self.set_session_files_memory_cache(key, payload, status)
                self.write_session_files_disk_cache_unlocked(path, signature, payload, status)
                self.record_performance_sample(
                    BACKGROUND_ROLE_SESSION_FILES,
                    "cache-entry",
                    trigger="compute",
                    compute_ms=(time.perf_counter() - started) * 1000,
                    payload=payload,
                    cache_key=key,
                    cache_status="miss:computed",
                    cache_hit=False,
                    cache_fresh=True,
                )
                return copy.deepcopy(payload), status, False, 0.0
        except OSError as exc:
            logger.warning("failed to lock session-files cache %s: %s", path, exc)
            payload, status = compute()
            self.set_session_files_memory_cache(key, payload, status)
            self.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "cache-entry",
                trigger="compute-lock-fallback",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=payload,
                cache_key=key,
                cache_status="miss:lock-fallback",
                cache_hit=False,
                cache_fresh=True,
            )
            return copy.deepcopy(payload), status, False, 0.0

    def get_session_files_cache(
        self,
        key: tuple[Any, ...],
        max_age_seconds: float | None = None,
        allow_stale: bool = False,
    ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
        started = time.perf_counter()
        now = time.monotonic()
        stale_cached: tuple[SessionFilesPayload, HTTPStatus, bool, float] | None = None
        with self.session_files_cache_lock:
            cached = self.session_files_cache.get(key)
            if cached:
                stored_at, value = cached
                age_seconds = max(0.0, now - stored_at)
                fresh = max_age_seconds is None or age_seconds <= max_age_seconds
                payload, status = value
                if fresh:
                    self.record_performance_sample(
                        BACKGROUND_ROLE_SESSION_FILES,
                        "cache-read",
                        trigger="memory",
                        compute_ms=(time.perf_counter() - started) * 1000,
                        payload=payload,
                        cache_key=key,
                        cache_status="hit:fresh",
                        cache_hit=True,
                        cache_fresh=True,
                    )
                    return copy.deepcopy(payload), status, True, age_seconds
                stale_cached = (copy.deepcopy(payload), status, False, age_seconds)
        disk_cached = self.read_session_files_disk_cache(key, max_age_seconds=max_age_seconds, allow_stale=allow_stale)
        if disk_cached:
            if stale_cached is None or disk_cached[3] <= stale_cached[3]:
                self.record_performance_sample(
                    BACKGROUND_ROLE_SESSION_FILES,
                    "cache-read",
                    trigger="disk",
                    compute_ms=(time.perf_counter() - started) * 1000,
                    payload=disk_cached[0],
                    cache_key=key,
                    cache_status="hit:fresh" if disk_cached[2] else "hit:stale",
                    cache_hit=True,
                    cache_fresh=bool(disk_cached[2]),
                )
                return disk_cached
        if stale_cached is not None and allow_stale:
            self.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "cache-read",
                trigger="memory",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=stale_cached[0],
                cache_key=key,
                cache_status="hit:stale",
                cache_hit=True,
                cache_fresh=False,
            )
            return stale_cached
        self.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            "cache-read",
            trigger="miss",
            compute_ms=(time.perf_counter() - started) * 1000,
            cache_key=key,
            cache_status="miss",
            cache_hit=False,
        )
        return None

    def set_session_files_cache(self, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
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
        started = time.perf_counter()
        refresh_details = self.background_refresh_event_details(BACKGROUND_ROLE_SESSION_FILES, {"session": session or ""}, cache_key=cache_key)
        self.log_sampled_background_refresh_event(
            "background_refresh_started",
            BACKGROUND_ROLE_SESSION_FILES,
            "Session-files background refresh started",
            refresh_details,
        )
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
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            self.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh finished",
                done_details,
            )
            self.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {**refresh_details, "compute_ms": compute_ms})
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
        started = time.perf_counter()
        refresh_details = self.background_refresh_event_details(BACKGROUND_ROLE_SESSION_FILES, {"session": info.session}, cache_key=cache_key)
        self.log_sampled_background_refresh_event(
            "background_refresh_started",
            BACKGROUND_ROLE_SESSION_FILES,
            "Session-files background refresh started",
            refresh_details,
        )
        try:
            self.compute_session_files_cache_entry(
                cache_key,
                lambda: (session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs), HTTPStatus.OK),
            )
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            self.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh finished",
                done_details,
            )
            self.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {**refresh_details, "compute_ms": compute_ms})
        finally:
            with self.session_files_cache_lock:
                self.session_files_refreshing_cache_keys.discard(cache_key)

    def start_session_files_cache_refresh(self, cache_key: tuple[Any, ...], target: Any, *args: Any) -> bool:
        if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"cache_key": repr(cache_key)})
            return False
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
    ) -> SessionFilesPayload:
        infos = {info.session: info}
        key = self.session_files_cache_key("payload", infos, info.session, hours, from_ref, to_ref, repo_refs)
        cached = self.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=True)
        if cached:
            payload, _status, fresh, _age = cached
            if not fresh:
                if self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
                    self.start_session_files_cache_refresh(key, self.refresh_session_files_info_cache, info, hours, from_ref, to_ref, repo_refs)
                else:
                    self.record_background_follower_stale_read(BACKGROUND_ROLE_SESSION_FILES)
                    refresh_result = self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"session": info.session, "cache_key": repr(key)})
                    self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                    if self.background_refresh_should_fallback(refresh_result):
                        payload, _status, _hit, _age = self.compute_session_files_cache_entry(
                            key,
                            lambda: (session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs), HTTPStatus.OK),
                        )
            return payload
        if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            refresh_result = self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"session": info.session, "cache_key": repr(key)})
            self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
            if self.background_refresh_should_fallback(refresh_result):
                payload, _status, _hit, _age = self.compute_session_files_cache_entry(
                    key,
                    lambda: (session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs), HTTPStatus.OK),
                )
                return copy.deepcopy(payload)
            return {"files": [], "repos": [], "errors": [], "refreshing_elsewhere": True}
        payload, _status, _hit, _age = self.compute_session_files_cache_entry(
            key,
            lambda: (session_files.session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs), HTTPStatus.OK),
        )
        return copy.deepcopy(payload)

    def warm_start_session_files_payload_cache(self) -> None:
        if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"reason": "warm-start"})
            return
        sessions, _errors = discover_sessions(self.sessions)
        for session in self.sessions:
            info = sessions.get(session)
            if info is not None and info.agents:
                key = self.session_files_cache_key("payload", {session: info}, session, 24.0, None, None, None)
                self.get_session_files_cache(key, max_age_seconds=None, allow_stale=True)

    def warm_start_tabber_activity_cache(self) -> None:
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "warm-start"})
            return
        source_signature = self.tabber_activity_source_signature()
        self.get_tabber_activity_cache(float("inf"), allow_stale=True, hours=24.0, source_signature=source_signature)

    def cached_session_files_payloads_for_infos(
        self,
        infos: dict[str, SessionInfo],
        hours: float = 24.0,
        from_ref: str | None = None,
        to_ref: str | None = None,
        repo_refs: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, SessionFilesPayload]:
        if not infos:
            return {}
        if len(infos) == 1:
            session, info = next(iter(infos.items()))
            return {session: self.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)}
        if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            return {
                session: self.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
                for session, info in infos.items()
            }
        payloads: dict[str, SessionFilesPayload] = {}
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
    ) -> tuple[SessionFilesPayload, HTTPStatus]:
        started = time.perf_counter()
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
                if self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
                    refreshing = self.start_session_files_cache_refresh(cache_key, self.refresh_session_files_payload_cache, session, infos, hours, from_ref, to_ref, repo_refs)
                    cache_meta["refreshing"] = refreshing
                else:
                    self.record_background_follower_stale_read(BACKGROUND_ROLE_SESSION_FILES)
                    refresh_result = self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"session": session or "", "cache_key": repr(cache_key)})
                    self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                    if self.background_refresh_should_fallback(refresh_result):
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
                            "fallback": True,
                        }
                    else:
                        cache_meta["refreshing_elsewhere"] = True
        else:
            if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
                refresh_result = self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"session": session or "", "cache_key": repr(cache_key)})
                self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                if self.background_refresh_should_fallback(refresh_result):
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
                        "fallback": True,
                    }
                else:
                    info = infos.get(session) if session else None
                    if info is not None:
                        payload = session_files.refreshing_session_files_payload_for_info(
                            info,
                            hours=hours,
                            from_ref=from_ref,
                            to_ref=to_ref,
                            repo_refs=repo_refs,
                        )
                    else:
                        payload = {"session": session or "", "files": [], "repos": [], "errors": [], "refreshing_elsewhere": True}
                    status = HTTPStatus.OK
                    cache_meta = {
                        "hit": False,
                        "stale": True,
                        "age_seconds": None,
                        "refresh_seconds": max_age,
                        "refreshing_elsewhere": True,
                    }
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
        self.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            "payload",
            trigger="force" if force else "request",
            compute_ms=(time.perf_counter() - started) * 1000,
            payload=payload,
            cache_key=cache_key,
            cache_status="hit:stale" if cache_meta.get("hit") and cache_meta.get("stale") else ("hit:fresh" if cache_meta.get("hit") else ("refreshing-elsewhere" if cache_meta.get("refreshing_elsewhere") else "miss:computed")),
            cache_hit=bool(cache_meta.get("hit")),
            cache_fresh=not bool(cache_meta.get("stale")),
            details={"session": session or "", "status": int(status)},
        )
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

    def start_transcripts_payload_refresh(self, publish: bool = False, defer: bool = False) -> bool:
        with self.transcripts_payload_cache_lock:
            if self.transcripts_payload_refreshing:
                return False
            self.transcripts_payload_refreshing = True
        if defer:
            worker = threading.Timer(0.05, self.refresh_transcripts_payload_cache, args=(publish,))
            worker.daemon = True
        else:
            worker = threading.Thread(target=self.refresh_transcripts_payload_cache, args=(publish,), daemon=True)
        worker.start()
        return True

    def refresh_transcripts_payload_cache(self, publish: bool = False) -> None:
        try:
            payload = self.build_transcripts_payload()
            self.set_transcripts_payload_cache(payload)
            if publish:
                payload_signature = self.transcripts_payload_event_signature(payload)
                with self.client_watch_lock:
                    self.client_watch_transcripts_payload_signature = payload_signature
                self.publish_client_event(
                    "transcripts_changed",
                    {"data": payload},
                    trigger="transcripts_refresh",
                    cache="ready",
                )
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
        source_signature = self.tabber_activity_source_signature()
        cached = self.get_tabber_activity_cache(self.tabber_activity_refresh_seconds(), allow_stale=True, source_signature=source_signature)
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
        recent_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selected = info.selected_pane
        agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        git_data = project_git(project)
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
            "recent_events": recent_events if recent_events is not None else self.event_log.tail(session=session, limit=5),
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
        session_files_by_session: dict[str, SessionFilesPayload] = {}
        session_info: dict[str, Any] = {}
        recent_events_by_session = self.event_log.tail_many([session for session in ordered_sessions if session in sessions], limit=5)
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
                session_info[session] = self.activity_session_info_payload(
                    session,
                    info,
                    project,
                    files_payload,
                    summary,
                    recent_events=recent_events_by_session.get(session, []),
                )
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
        stream_items_sanitized: bool = False,
    ) -> None:
        self.yoagent_streams.publish_delta(
            stream_id,
            content,
            backend=backend,
            phase=phase,
            done=done,
            hidden_thinking_removed=hidden_thinking_removed,
            events=events,
            auxiliary_lines=auxiliary_lines,
            auxiliary_preview=auxiliary_preview,
            stream_items=stream_items,
            hidden_work_active=hidden_work_active,
            tool_active=tool_active,
            auxiliary_done=auxiliary_done,
            auxiliary_truncated=auxiliary_truncated,
            turn_done=turn_done,
            error=error,
            aborted=aborted,
            created_at=created_at,
            stream_items_sanitized=stream_items_sanitized,
        )

    def yoagent_stream_auxiliary_message_fields(self, stream_id: str) -> dict[str, Any]:
        return self.yoagent_streams.auxiliary_message_fields(stream_id)

    def sanitized_yoagent_stream_items(self, value: Any) -> list[dict[str, str]]:
        return sanitized_yoagent_stream_items(value)

    def yoagent_stream_callback(self, stream_id: str, backend: str) -> Any:
        return self.yoagent_streams.callback_for(stream_id, backend)

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

    def yoagent_visible_composer_source(self, *args: Any, **kwargs: Any) -> Any:
        return self.yoagent_controller.yoagent_visible_composer_source(*args, **kwargs)

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
        self.sync_tmux_theme_from_settings(payload, force=patch_updates_active_color(patch))
        self.publish_client_event("settings_changed", {"mtime_ns": payload.get("mtime_ns", 0), "data": payload}, trigger="manual", cache="ready")
        self.client_watch_wake_event.set()
        return payload

    def sync_tmux_theme_from_settings(self, payload: dict[str, Any], force: bool = False) -> dict[str, Any] | None:
        color = tmux_theme_color_from_settings(payload.get("settings") if isinstance(payload, dict) else None)
        if not force and color == self.tmux_theme_color:
            return None
        result = apply_tmux_theme_color_to_existing(color, runner=tmux)
        self.tmux_theme_color = color
        if result.get("errors"):
            logger.debug("tmux theme sync failed for %s: %s", color, result.get("errors"))
        return result

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

    def background_cache_key_summary(self, cache_key: Any) -> dict[str, Any]:
        if cache_key in (None, ""):
            return {}
        try:
            raw = json.dumps(cache_key, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            raw = repr(cache_key)
        summary = {
            "cache_key_hash": hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16],
        }
        cache_key_kind = self.performance_cache_key_kind(cache_key)
        if cache_key_kind:
            summary["cache_key_kind"] = cache_key_kind
        return summary

    def background_refresh_event_details(
        self,
        role: str,
        payload: dict[str, Any] | None = None,
        *,
        cache_key: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {"role": role}
        request_payload = payload if isinstance(payload, dict) else {}
        for key in ("session", "reason", "trigger", "cache_key_kind"):
            value = request_payload.get(key)
            if value not in (None, ""):
                details[key] = truncate_text(str(value), 160)
        selected_cache_key = request_payload.get("cache_key") if cache_key in (None, "") else cache_key
        details.update(self.background_cache_key_summary(selected_cache_key))
        if extra:
            for key, value in extra.items():
                if value in (None, "") or key == "cache_key":
                    continue
                if isinstance(value, str):
                    details[key] = truncate_text(value, 160)
                elif isinstance(value, (int, float, bool)):
                    details[key] = value
        return details

    def log_sampled_background_refresh_event(self, event_type: str, role: str, message: str, details: dict[str, Any]) -> dict[str, Any] | None:
        key = (event_type, role)
        with self.background_refresh_event_log_lock:
            count = self.background_refresh_event_log_counts.get(key, 0) + 1
            self.background_refresh_event_log_counts[key] = count
            should_emit = count == 1 or count % BACKGROUND_REFRESH_EVENT_LOG_SAMPLE_EVERY == 0
            if not should_emit:
                return None
            previous_emit_count = self.background_refresh_event_log_last_emit_counts.get(key, 0)
            self.background_refresh_event_log_last_emit_counts[key] = count
        event_details = dict(details)
        event_details["sample_count"] = count
        suppressed = max(0, count - previous_emit_count - 1)
        if suppressed:
            event_details["suppressed_since_last"] = suppressed
        return self.log_event(None, event_type, message, event_details)

    def performance_payload_bytes(self, payload: Any) -> int:
        try:
            return len(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return len(str(payload).encode("utf-8", errors="replace"))

    def performance_cache_key_kind(self, cache_key: Any) -> str:
        if isinstance(cache_key, tuple) and cache_key:
            return str(cache_key[0] or "")[:80]
        if isinstance(cache_key, dict):
            for key in ("kind", "cache_key_kind", "role"):
                value = str(cache_key.get(key) or "").strip()
                if value:
                    return value[:80]
        return str(cache_key or "")[:80]

    def performance_owner_role(self, role: str) -> str:
        if role in {BACKGROUND_ROLE_SESSION_FILES, BACKGROUND_ROLE_STATS_SAMPLER, BACKGROUND_ROLE_TABBER_ACTIVITY, BACKGROUND_ROLE_SEARCH_INDEX, BACKGROUND_ROLE_WATCH_ROOTS}:
            return "owner" if self.background_can_run(role) else "follower"
        return ""

    def record_performance_sample(
        self,
        role: str,
        surface: str,
        *,
        trigger: str = "",
        compute_ms: float | None = None,
        payload: Any = None,
        payload_bytes: int | None = None,
        cache_key: Any = None,
        cache_status: str = "",
        cache_hit: bool | None = None,
        cache_fresh: bool | None = None,
        owner_role: str = "",
        count: int | None = None,
        details: dict[str, Any] | None = None,
        record_time: float | None = None,
    ) -> dict[str, Any]:
        if payload_bytes is None and payload is not None:
            payload_bytes = self.performance_payload_bytes(payload)
        item: dict[str, Any] = {
            "time": float(record_time) if record_time is not None else time.time(),
            "role": str(role or "")[:80],
            "surface": str(surface or "")[:120],
            "trigger": str(trigger or "")[:120],
            "owner_role": str(owner_role or self.performance_owner_role(str(role or "")))[:40],
            "compute_ms": round(max(0.0, float(compute_ms or 0.0)), 3),
            "payload_bytes": max(0, int(payload_bytes or 0)),
            "cache_key_kind": self.performance_cache_key_kind(cache_key),
            "cache_status": str(cache_status or "")[:80],
        }
        if cache_hit is not None:
            item["cache_hit"] = bool(cache_hit)
        if cache_fresh is not None:
            item["cache_fresh"] = bool(cache_fresh)
            item["cache_stale"] = not bool(cache_fresh)
        if count is not None:
            item["count"] = max(0, int(count))
        if details:
            item["details"] = {
                str(key): truncate_text(value, 500) if isinstance(value, str) else value
                for key, value in details.items()
                if isinstance(value, (str, int, float, bool))
            }
        with self.performance_record_lock:
            self.performance_records.append(item)
        return item

    def performance_metrics_payload(self, window_seconds: float = PERFORMANCE_SUMMARY_WINDOW_SECONDS) -> dict[str, Any]:
        now = time.time()
        cutoff = now - max(1.0, float(window_seconds or PERFORMANCE_SUMMARY_WINDOW_SECONDS))
        with self.performance_record_lock:
            records = [dict(item) for item in self.performance_records]
        window_records = [item for item in records if self.float_value(item.get("time"), 0.0) >= cutoff]
        summaries: dict[tuple[str, str], dict[str, Any]] = {}
        for item in window_records:
            key = (str(item.get("role") or ""), str(item.get("surface") or ""))
            summary = summaries.setdefault(key, {
                "role": key[0],
                "surface": key[1],
                "count": 0,
                "compute_ms_total": 0.0,
                "compute_ms_max": 0.0,
                "payload_bytes_total": 0,
                "cache": {},
            })
            summary["count"] += 1
            compute_ms = max(0.0, self.float_value(item.get("compute_ms"), 0.0))
            summary["compute_ms_total"] += compute_ms
            summary["compute_ms_max"] = max(summary["compute_ms_max"], compute_ms)
            summary["payload_bytes_total"] += max(0, int(self.float_value(item.get("payload_bytes"), 0.0)))
            cache_status = str(item.get("cache_status") or "")
            if cache_status:
                summary["cache"][cache_status] = int(summary["cache"].get(cache_status, 0)) + 1
        summary_rows = []
        for item in summaries.values():
            count = max(1, int(item["count"]))
            summary_rows.append({
                "role": item["role"],
                "surface": item["surface"],
                "count": item["count"],
                "compute_ms_avg": round(float(item["compute_ms_total"]) / count, 3),
                "compute_ms_max": round(float(item["compute_ms_max"]), 3),
                "payload_bytes_total": item["payload_bytes_total"],
                "cache": item["cache"],
            })
        summary_rows.sort(key=lambda item: (-float(item["compute_ms_max"]), item["role"], item["surface"]))
        top_payload_rows = sorted(
            summary_rows,
            key=lambda item: (-int(item["payload_bytes_total"]), -int(item["count"]), item["role"], item["surface"]),
        )
        return {
            "window_seconds": max(1.0, float(window_seconds or PERFORMANCE_SUMMARY_WINDOW_SECONDS)),
            "record_limit": PERFORMANCE_RECORD_LIMIT,
            "record_count": len(records),
            "summary": summary_rows,
            "top_payload_bytes": top_payload_rows,
            "recent": records[-PERFORMANCE_RECENT_LIMIT:],
        }

    def runtime_cache_dir_stats(self, path: Path) -> dict[str, Any]:
        root = Path(path)
        stats = {"path": str(root), "exists": root.exists(), "files": 0, "dirs": 0, "bytes": 0, "errors": 0}
        if not stats["exists"]:
            return stats
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stats["dirs"] += 1
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                stats["files"] += 1
                                stats["bytes"] += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            stats["errors"] += 1
            except OSError:
                stats["errors"] += 1
        return stats

    def runtime_top_event_types(self, limit: int = 500) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        events = self.event_log.tail(limit=max(1, min(int(limit or 500), MAX_EVENT_TAIL_LINES)))
        for event in events:
            event_type = str(event.get("type") or "event")
            counts[event_type] = counts.get(event_type, 0) + 1
        return [
            {"type": event_type, "count": count}
            for event_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]
        ]

    def runtime_largest_transcripts(self, transcript_payload: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        sessions = transcript_payload.get("sessions") if isinstance(transcript_payload, dict) else {}
        if not isinstance(sessions, dict):
            return rows
        for session, info in sessions.items():
            if not isinstance(info, dict):
                continue
            agents = info.get("agents")
            if not isinstance(agents, list):
                continue
            for agent in agents:
                if not isinstance(agent, dict):
                    continue
                transcript = str(agent.get("transcript") or "")
                if not transcript:
                    continue
                path = Path(transcript)
                try:
                    stat = path.stat()
                    size = stat.st_size
                    mtime = stat.st_mtime
                    exists = True
                except OSError:
                    size = 0
                    mtime = 0.0
                    exists = False
                rows.append({
                    "session": str(session),
                    "kind": str(agent.get("kind") or ""),
                    "pid": agent.get("pid") if isinstance(agent.get("pid"), int) else 0,
                    "path": transcript,
                    "bytes": size,
                    "mtime": mtime,
                    "exists": exists,
                })
        rows.sort(key=lambda item: (-int(item["bytes"]), item["session"], item["path"]))
        return rows[:max(1, int(limit or 8))]

    def runtime_top_endpoints(self, background_status: dict[str, Any]) -> list[dict[str, Any]]:
        perf = background_status.get("perf") if isinstance(background_status, dict) else {}
        if not isinstance(perf, dict):
            return []
        rows = perf.get("top_payload_bytes")
        if not isinstance(rows, list):
            rows = perf.get("summary")
        if not isinstance(rows, list):
            return []
        endpoints = [dict(row) for row in rows if isinstance(row, dict) and row.get("role") == "http-endpoint"]
        endpoints.sort(key=lambda item: (-int(item.get("payload_bytes_total") or 0), -int(item.get("count") or 0), str(item.get("surface") or "")))
        return endpoints[:8]

    def runtime_top_background_work(self, background_status: dict[str, Any]) -> list[dict[str, Any]]:
        perf = background_status.get("perf") if isinstance(background_status, dict) else {}
        if not isinstance(perf, dict):
            return []
        rows = perf.get("summary")
        if not isinstance(rows, list):
            rows = perf.get("top_payload_bytes")
        if not isinstance(rows, list):
            return []
        background_rows = [
            dict(row)
            for row in rows
            if isinstance(row, dict) and row.get("role") and row.get("role") != "http-endpoint"
        ]
        background_rows.sort(key=lambda item: (
            -float(item.get("compute_ms_max") or 0.0),
            -int(item.get("payload_bytes_total") or 0),
            -int(item.get("count") or 0),
            str(item.get("role") or ""),
            str(item.get("surface") or ""),
        ))
        return background_rows[:12]

    def runtime_refresh_state(self, background_status: dict[str, Any]) -> dict[str, Any]:
        with self.session_files_cache_lock:
            session_files_refreshing_count = len(self.session_files_refreshing_cache_keys)
        with self.tabber_activity_cache_lock:
            tabber_activity_refreshing = self.tabber_activity_cache_refreshing
            tabber_warmer_running = self.tabber_activity_cache_warmer_running
        with self.transcripts_payload_cache_lock:
            transcripts_payload_refreshing = self.transcripts_payload_refreshing
        return {
            "roles": background_status.get("roles", {}) if isinstance(background_status, dict) else {},
            "counters": background_status.get("counters", {}) if isinstance(background_status, dict) else {},
            "coalescing": background_status.get("refresh_queue", {}) if isinstance(background_status, dict) else {},
            "local_refreshing": {
                "session_files": session_files_refreshing_count,
                "tabber_activity": tabber_activity_refreshing,
                "tabber_warmer": tabber_warmer_running,
                "transcripts_payload": transcripts_payload_refreshing,
            },
        }

    def runtime_owner_debug_summary(self, owner_debug: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(owner_debug, dict):
            return {}
        generations = owner_debug.get("generations")
        return {
            "owner_dir": str(owner_debug.get("owner_dir") or ""),
            "generation_count": len(generations) if isinstance(generations, list) else 0,
        }

    def runtime_owner_control_summary(self, owner_control_response: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(owner_control_response, dict):
            return {}
        summary = {"ok": bool(owner_control_response.get("ok"))}
        error = str(owner_control_response.get("error") or "")
        if error:
            summary["error"] = error
        return summary

    def runtime_report_payload(
        self,
        *,
        background_status: dict[str, Any] | None = None,
        owner_debug: dict[str, Any] | None = None,
        owner_control_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = background_status if isinstance(background_status, dict) else self.background_owner.status_payload()
        transcript_payload = self.transcripts_payload(force=True)
        return {
            "ok": True,
            "state_dir": str(common.STATE_DIR),
            "owner": {
                "current_owner": status.get("current_owner"),
                "status": status.get("status"),
                "owner": bool(status.get("owner")),
                "search_index": status.get("search_index"),
                "debug": self.runtime_owner_debug_summary(owner_debug),
                "control": self.runtime_owner_control_summary(owner_control_response),
            },
            "refresh": self.runtime_refresh_state(status),
            "caches": {
                "session_files": self.runtime_cache_dir_stats(SESSION_FILES_CACHE_DIR),
                "activity": self.runtime_cache_dir_stats(TABBER_ACTIVITY_CACHE_DIR),
                "search_index": self.runtime_cache_dir_stats(file_index.INDEX_DIR),
            },
            "top_endpoints": self.runtime_top_endpoints(status),
            "top_background_work": self.runtime_top_background_work(status),
            "top_event_types": self.runtime_top_event_types(),
            "largest_active_transcripts": self.runtime_largest_transcripts(transcript_payload),
            "transcripts_cache": transcript_payload.get("cache", {}) if isinstance(transcript_payload, dict) else {},
        }

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

    def cached_active_window_for(self, session: str) -> str | None:
        clean_session = str(session or "").strip()
        if not clean_session:
            return None
        with self.transcripts_payload_cache_lock:
            cached = self.transcripts_payload_cache
            payload = cached[1] if cached is not None else None
            info = (payload.get("sessions") or {}).get(clean_session) if isinstance(payload, dict) else None
            panes = info.get("panes") if isinstance(info, dict) else None
            if isinstance(panes, list):
                window = active_window_for_panes(panes)
                if window not in (None, ""):
                    return window
        return None

    def active_window_for(self, session: str) -> str | None:
        """Active window for non-hot-path callers; input heartbeats use cached metadata only."""
        window = self.cached_active_window_for(session)
        if window not in (None, ""):
            return window
        result = tmux(["display-message", "-p", "-t", tmux_session_target(session), "#{window_index}"], timeout=1.0)
        if result.returncode != 0:
            return None
        window = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        return window or None

    def start_input_heartbeat_worker(self) -> None:
        with self.input_heartbeat_condition:
            worker = self.input_heartbeat_worker_thread
            if worker is not None and worker.is_alive():
                return
            self.input_heartbeat_worker_stop = False
            worker = threading.Thread(target=self.input_heartbeat_worker_loop, name="input-heartbeats", daemon=True)
            self.input_heartbeat_worker_thread = worker
            worker.start()

    def _take_input_heartbeat_batch_locked(self) -> list[PendingInputHeartbeat]:
        batch = list(self.input_heartbeat_pending.values())
        self.input_heartbeat_pending = {}
        self.input_heartbeat_flush_active = bool(batch)
        return batch

    def _finish_input_heartbeat_flush(self) -> None:
        with self.input_heartbeat_condition:
            self.input_heartbeat_flush_active = False
            self.input_heartbeat_condition.notify_all()

    def flush_input_heartbeat_batch(self, batch: list[PendingInputHeartbeat]) -> None:
        if not batch:
            return
        by_session_window: dict[tuple[str, str | None, str], PendingInputHeartbeat] = {}
        window_by_session: dict[str, str | None] = {}
        needs_cache_refresh = False
        for item in batch:
            window = window_by_session.get(item.session)
            if item.session not in window_by_session:
                window = self.cached_active_window_for(item.session)
                window_by_session[item.session] = window
                if window is None:
                    needs_cache_refresh = True
            key = (item.session, window, item.source)
            existing = by_session_window.get(key)
            if existing is None:
                by_session_window[key] = PendingInputHeartbeat(item.session, item.source, item.byte_count, item.ts)
            else:
                existing.byte_count += item.byte_count
                existing.ts = max(existing.ts, item.ts)
        if needs_cache_refresh:
            self.start_transcripts_payload_refresh(defer=True)
        for (session, window, source), item in by_session_window.items():
            self.activity_ledger.heartbeat(session, window, ts=item.ts, byte_count=item.byte_count, source=source)

    def input_heartbeat_worker_loop(self) -> None:
        while True:
            with self.input_heartbeat_condition:
                while (not self.input_heartbeat_pending or self.input_heartbeat_flush_active) and not self.input_heartbeat_worker_stop:
                    self.input_heartbeat_condition.wait()
                if self.input_heartbeat_worker_stop and not self.input_heartbeat_pending:
                    return
                self.input_heartbeat_condition.wait(max(0.0, INPUT_HEARTBEAT_COALESCE_SECONDS))
                while self.input_heartbeat_flush_active and not self.input_heartbeat_worker_stop:
                    self.input_heartbeat_condition.wait()
                batch = self._take_input_heartbeat_batch_locked()
            try:
                self.flush_input_heartbeat_batch(batch)
            finally:
                self._finish_input_heartbeat_flush()

    def flush_input_heartbeats(self, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self.input_heartbeat_condition:
                while self.input_heartbeat_flush_active:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self.input_heartbeat_condition.wait(remaining)
                if not self.input_heartbeat_pending:
                    return True
                batch = self._take_input_heartbeat_batch_locked()
            try:
                self.flush_input_heartbeat_batch(batch)
            finally:
                self._finish_input_heartbeat_flush()

    def stop_input_heartbeat_worker(self) -> None:
        self.flush_input_heartbeats()
        with self.input_heartbeat_condition:
            self.input_heartbeat_worker_stop = True
            self.input_heartbeat_condition.notify_all()
            worker = self.input_heartbeat_worker_thread
        if worker is not None:
            worker.join(timeout=1.0)
        self.flush_input_heartbeats()

    def record_user_input(self, session: str, byte_count: int, source: str = "host", data: str = "") -> None:
        """Queue one user-input heartbeat from the WS bridge without touching tmux or disk."""
        clean_session = str(session or "").strip()
        if not clean_session:
            return
        if data and not terminal_input_counts_as_user_activity(data):
            return
        worker = self.input_heartbeat_worker_thread
        if worker is None or not worker.is_alive():
            self.start_input_heartbeat_worker()
        clean_source = str(source or "host")
        count = max(0, int(byte_count or 0))
        with self.input_heartbeat_condition:
            key = (clean_session, clean_source)
            pending = self.input_heartbeat_pending.get(key)
            if pending is None:
                self.input_heartbeat_pending[key] = PendingInputHeartbeat(clean_session, clean_source, count, time.time())
            else:
                pending.byte_count += count
                pending.ts = time.time()
            self.input_heartbeat_condition.notify()

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
            "agent_windows": {
                session: self.agent_window_status_payloads(
                    session,
                    info=info,
                    discovered_sessions=sessions,
                    activity_snapshot=activity_snapshot,
                    files_payload=session_files_by_session.get(session),
                )
                for session, info in agent_infos.items()
            },
            "errors": errors,
            "session_scope": scope,
            "session_file_hours": bounded_hours,
        }

    def tabber_activity_source_signature(self, session_scope: Any = "configured") -> str:
        session_names, _scope_errors, scope = self.activity_session_names(session_scope)
        sessions, _errors = discover_sessions(session_names)
        rows = []
        for session in sorted(session_names):
            info = sessions.get(session)
            if info is None:
                rows.append((session, None))
                continue
            selected_path = info.selected_pane.current_path if info.selected_pane and info.selected_pane.current_path else ""
            rows.append((
                session,
                selected_path,
                tuple((agent.kind or "", agent.cwd or "", agent.transcript or "", agent.session_id or "") for agent in info.agents),
            ))
        key_text = self.client_event_payload_signature({"scope": scope, "sessions": rows})
        return hashlib.sha256(key_text.encode("utf-8")).hexdigest()

    def tabber_activity_cache_disk_path(self, hours: float, source_signature: str = "") -> tuple[Path, str]:
        key_text = json.dumps(
            {
                "kind": "tabber-activity",
                "hours": session_files.bounded_session_files_hours(hours),
                "source_signature": source_signature,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        signature = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        return TABBER_ACTIVITY_CACHE_DIR / f"{signature}.json", signature

    def tabber_activity_cache_manifest_path(self, signature: str) -> Path:
        return TABBER_ACTIVITY_CACHE_DIR / f"{signature}.manifest.json"

    def read_tabber_activity_disk_cache(
        self,
        hours: float,
        max_age_seconds: float | None = None,
        allow_stale: bool = True,
        source_signature: str = "",
    ) -> tuple[dict[str, Any], bool, float] | None:
        path, signature = self.tabber_activity_cache_disk_path(hours, source_signature)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            return None
        if not isinstance(record, dict):
            return None
        if record.get("version") != TABBER_ACTIVITY_CACHE_VERSION or record.get("signature") != signature:
            return None
        if str(record.get("source_signature") or "") != source_signature:
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        payload_signature = str(record.get("payload_signature") or self.session_files_payload_signature(payload))
        manifest_path = self.tabber_activity_cache_manifest_path(signature)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            manifest = None
        try:
            stored_at_wall = float(record.get("stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        if isinstance(manifest, dict) and manifest.get("version") == TABBER_ACTIVITY_CACHE_VERSION and manifest.get("signature") == signature and manifest.get("payload_signature") == payload_signature and str(manifest.get("source_signature") or "") == source_signature:
            try:
                stored_at_wall = float(manifest.get("stored_at", stored_at_wall))
            except (TypeError, ValueError):
                pass
        cached_hours = session_files.bounded_session_files_hours(self.float_value(payload.get("session_file_hours"), 24.0))
        if cached_hours != session_files.bounded_session_files_hours(hours):
            return None
        age_seconds = max(0.0, time.time() - stored_at_wall)
        fresh = max_age_seconds is None or age_seconds <= max_age_seconds
        if not fresh and not allow_stale:
            return None
        self.set_tabber_activity_cache(payload, stored_at=time.monotonic() - age_seconds, write_disk=False, source_signature=source_signature)
        return copy.deepcopy(payload), fresh, age_seconds

    def write_tabber_activity_disk_cache_unlocked(self, path: Path, signature: str, payload: dict[str, Any], source_signature: str) -> None:
        payload_signature = self.session_files_payload_signature(payload)
        payload_changed = True
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            existing = None
        if isinstance(existing, dict) and existing.get("version") == TABBER_ACTIVITY_CACHE_VERSION and existing.get("signature") == signature:
            if str(existing.get("source_signature") or "") != source_signature:
                existing = None
        if isinstance(existing, dict) and existing.get("version") == TABBER_ACTIVITY_CACHE_VERSION and existing.get("signature") == signature:
            existing_payload = existing.get("payload")
            existing_payload_signature = str(existing.get("payload_signature") or "")
            if not existing_payload_signature and isinstance(existing_payload, dict):
                existing_payload_signature = self.session_files_payload_signature(existing_payload)
            payload_changed = existing_payload_signature != payload_signature
        stored_at = time.time()
        record = {
            "version": TABBER_ACTIVITY_CACHE_VERSION,
            "signature": signature,
            "source_signature": source_signature,
            "stored_at": stored_at,
            "payload_signature": payload_signature,
            "payload": payload,
        }
        if payload_changed:
            atomic_write_text(path, json.dumps(record, sort_keys=True, separators=(",", ":")), mode=0o600)
        manifest = {
            "version": TABBER_ACTIVITY_CACHE_VERSION,
            "signature": signature,
            "source_signature": source_signature,
            "stored_at": stored_at,
            "payload_signature": payload_signature,
            "payload_changed": payload_changed,
            "owner": self.background_owner.status_payload().get("generation", {}),
            "refresh_status": "ready",
            "last_error": "",
        }
        atomic_write_text(self.tabber_activity_cache_manifest_path(signature), json.dumps(manifest, sort_keys=True, separators=(",", ":")), mode=0o600)

    def write_tabber_activity_disk_cache(self, payload: dict[str, Any], source_signature: str = "") -> None:
        if not source_signature:
            source_signature = self.tabber_activity_source_signature()
        hours = session_files.bounded_session_files_hours(self.float_value(payload.get("session_file_hours"), 24.0))
        path, signature = self.tabber_activity_cache_disk_path(hours, source_signature)
        try:
            with file_lock(path, dir_mode=0o700):
                self.write_tabber_activity_disk_cache_unlocked(path, signature, payload, source_signature)
        except OSError as exc:
            logger.warning("failed to write tabber activity cache %s: %s", path, exc)

    def set_tabber_activity_cache(self, payload: dict[str, Any], stored_at: float | None = None, write_disk: bool = True, source_signature: str = "") -> None:
        if write_disk and not source_signature:
            source_signature = self.tabber_activity_source_signature()
        with self.tabber_activity_cache_lock:
            self.tabber_activity_cache = (time.monotonic() if stored_at is None else stored_at, copy.deepcopy(payload))
            self.tabber_activity_cache_source_signature = source_signature
        if write_disk:
            self.write_tabber_activity_disk_cache(payload, source_signature=source_signature)

    def get_tabber_activity_cache(self, max_age_seconds: float, allow_stale: bool = True, hours: float | None = None, source_signature: str = "") -> tuple[dict[str, Any], bool, float] | None:
        started = time.perf_counter()
        now = time.monotonic()
        bounded_hours = session_files.bounded_session_files_hours(24.0 if hours is None else hours)
        stale_cached: tuple[dict[str, Any], bool, float] | None = None
        with self.tabber_activity_cache_lock:
            cached = self.tabber_activity_cache
            if cached is not None:
                stored_at, payload = cached
                cached_hours = session_files.bounded_session_files_hours(self.float_value(payload.get("session_file_hours"), 24.0))
                if cached_hours == bounded_hours and (not source_signature or self.tabber_activity_cache_source_signature == source_signature):
                    age_seconds = max(0.0, now - stored_at)
                    fresh = age_seconds <= max_age_seconds
                    if fresh:
                        self.record_performance_sample(
                            BACKGROUND_ROLE_TABBER_ACTIVITY,
                            "cache-read",
                            trigger="memory",
                            compute_ms=(time.perf_counter() - started) * 1000,
                            payload=payload,
                            cache_key={"kind": "tabber-activity"},
                            cache_status="hit:fresh",
                            cache_hit=True,
                            cache_fresh=True,
                        )
                        return copy.deepcopy(payload), True, age_seconds
                    stale_cached = (copy.deepcopy(payload), False, age_seconds)
        disk_cached = self.read_tabber_activity_disk_cache(bounded_hours, max_age_seconds=max_age_seconds, allow_stale=allow_stale, source_signature=source_signature)
        if disk_cached and (stale_cached is None or disk_cached[2] <= stale_cached[2]):
            self.record_performance_sample(
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "cache-read",
                trigger="disk",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=disk_cached[0],
                cache_key={"kind": "tabber-activity"},
                cache_status="hit:fresh" if disk_cached[1] else "hit:stale",
                cache_hit=True,
                cache_fresh=bool(disk_cached[1]),
            )
            return disk_cached
        if stale_cached is not None and allow_stale:
            self.record_performance_sample(
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "cache-read",
                trigger="memory",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=stale_cached[0],
                cache_key={"kind": "tabber-activity"},
                cache_status="hit:stale",
                cache_hit=True,
                cache_fresh=False,
            )
            return stale_cached
        self.record_performance_sample(
            BACKGROUND_ROLE_TABBER_ACTIVITY,
            "cache-read",
            trigger="miss",
            compute_ms=(time.perf_counter() - started) * 1000,
            cache_key={"kind": "tabber-activity"},
            cache_status="miss",
            cache_hit=False,
        )
        return None

    def refresh_tabber_activity_cache(self, hours: Any = 24.0) -> dict[str, Any]:
        started = time.perf_counter()
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        source_signature = self.tabber_activity_source_signature()
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "refresh"})
            cached = self.get_tabber_activity_cache(float("inf"), allow_stale=True, hours=bounded_hours, source_signature=source_signature)
            if cached:
                payload, _fresh, _age = cached
                self.record_performance_sample(
                    BACKGROUND_ROLE_TABBER_ACTIVITY,
                    "refresh",
                    trigger="follower-cache",
                    compute_ms=(time.perf_counter() - started) * 1000,
                    payload=payload,
                    cache_key={"kind": "tabber-activity"},
                    cache_status="hit:follower",
                    cache_hit=True,
                )
                return payload
            payload = {"activity": {}, "agents": [], "agent_windows": {}, "errors": [], "session_scope": "configured", "session_file_hours": bounded_hours}
            self.record_performance_sample(
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "refresh",
                trigger="follower-empty",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=payload,
                cache_key={"kind": "tabber-activity"},
                cache_status="refreshing-elsewhere",
                cache_hit=False,
            )
            return payload
        payload = self.build_activity_payload(hours=bounded_hours)
        self.set_tabber_activity_cache(payload, source_signature=source_signature)
        self.record_performance_sample(
            BACKGROUND_ROLE_TABBER_ACTIVITY,
            "refresh",
            trigger="owner",
            compute_ms=(time.perf_counter() - started) * 1000,
            payload=payload,
            cache_key={"kind": "tabber-activity"},
            cache_status="computed",
            cache_hit=False,
            cache_fresh=True,
        )
        return payload

    def run_tabber_activity_cache_refresh(self) -> None:
        try:
            started = time.perf_counter()
            refresh_details = self.background_refresh_event_details(BACKGROUND_ROLE_TABBER_ACTIVITY, {"cache_key_kind": "tabber-activity"}, cache_key={"kind": "tabber-activity"})
            self.log_sampled_background_refresh_event(
                "background_refresh_started",
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "Tabber activity background refresh started",
                refresh_details,
            )
            self.refresh_tabber_activity_cache()
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            self.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "Tabber activity background refresh finished",
                done_details,
            )
            self.publish_background_refresh_done(BACKGROUND_ROLE_TABBER_ACTIVITY, {"compute_ms": compute_ms})
        finally:
            with self.tabber_activity_cache_lock:
                self.tabber_activity_cache_refreshing = False

    def start_tabber_activity_cache_refresh(self) -> bool:
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "async-refresh"})
            return False
        with self.tabber_activity_cache_lock:
            if self.tabber_activity_cache_refreshing:
                return False
            self.tabber_activity_cache_refreshing = True
        worker = threading.Thread(target=self.run_tabber_activity_cache_refresh, name="tabber-activity-refresh", daemon=True)
        worker.start()
        return True

    def start_tabber_activity_cache_warmer(self) -> bool:
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "warmer"})
            return False
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
                if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
                    return
                started = time.monotonic()
                refreshed = False
                try:
                    if self.tabber_activity_has_recent_consumer():
                        self.refresh_tabber_activity_cache()
                        refreshed = True
                    else:
                        self.record_performance_sample(
                            BACKGROUND_ROLE_TABBER_ACTIVITY,
                            "warmer",
                            trigger="idle",
                            cache_key={"kind": "tabber-activity"},
                            cache_status="skipped:no-consumer",
                        )
                except (OSError, RuntimeError, ValueError) as exc:
                    self.log_event(None, "client_event_watch_error", f"Tabber activity cache refresh failed: {exc}", {})
                interval = self.tabber_activity_refresh_seconds() if refreshed else self.tabber_activity_idle_refresh_seconds()
                elapsed = max(0.0, time.monotonic() - started)
                time.sleep(max(0.1, interval - elapsed))
        finally:
            with self.tabber_activity_cache_lock:
                self.tabber_activity_cache_warmer_running = False

    def activity_payload(self, hours: Any = 24.0, visible: bool = True) -> tuple[dict[str, Any], HTTPStatus]:
        visible_consumer = self.mark_tabber_activity_consumer(visible)
        refresh_seconds = self.tabber_activity_refresh_seconds()
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        source_signature = self.tabber_activity_source_signature()
        cached = self.get_tabber_activity_cache(refresh_seconds, allow_stale=True, hours=bounded_hours, source_signature=source_signature)
        if cached:
            payload, fresh, age_seconds = cached
            cached_hours = session_files.bounded_session_files_hours(self.float_value(payload.get("session_file_hours"), 24.0))
            if cached_hours != bounded_hours:
                payload = self.build_activity_payload(hours=bounded_hours)
                self.set_tabber_activity_cache(payload, source_signature=source_signature)
                payload = copy.deepcopy(payload)
                payload["cache"] = {
                    "hit": False,
                    "stale": False,
                    "age_seconds": 0,
                    "refresh_seconds": refresh_seconds,
                    "refreshing": False,
                }
                return payload, HTTPStatus.OK
            payload["cache"] = {
                "hit": True,
                "stale": not fresh,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": refresh_seconds,
            }
            if not fresh:
                if not visible_consumer:
                    payload["cache"]["refreshing"] = False
                    payload["cache"]["idle_no_consumer"] = True
                elif self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
                    payload["cache"]["refreshing"] = self.start_tabber_activity_cache_refresh()
                else:
                    self.record_background_follower_stale_read(BACKGROUND_ROLE_TABBER_ACTIVITY)
                    refresh_result = self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "activity-payload-stale"})
                    self.record_background_avoided_recompute(BACKGROUND_ROLE_TABBER_ACTIVITY)
                    if self.background_refresh_should_fallback(refresh_result):
                        payload = self.build_activity_payload(hours=bounded_hours)
                        self.set_tabber_activity_cache(payload, source_signature=source_signature)
                        payload = copy.deepcopy(payload)
                        payload["cache"] = {
                            "hit": False,
                            "stale": False,
                            "age_seconds": 0,
                            "refresh_seconds": refresh_seconds,
                            "fallback": True,
                        }
                    else:
                        payload["cache"]["refreshing_elsewhere"] = True
            return payload, HTTPStatus.OK
        if not visible_consumer:
            payload = {
                "activity": {},
                "agents": [],
                "agent_windows": {},
                "errors": [],
                "session_scope": "configured",
                "session_file_hours": bounded_hours,
                "cache": {
                    "hit": False,
                    "stale": True,
                    "age_seconds": None,
                    "refresh_seconds": refresh_seconds,
                    "idle_no_consumer": True,
                },
            }
            return payload, HTTPStatus.OK
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            refresh_result = self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "activity-payload"})
            self.record_background_avoided_recompute(BACKGROUND_ROLE_TABBER_ACTIVITY)
            if self.background_refresh_should_fallback(refresh_result):
                payload = self.build_activity_payload(hours=bounded_hours)
                self.set_tabber_activity_cache(payload, source_signature=source_signature)
                payload = copy.deepcopy(payload)
                payload["cache"] = {
                    "hit": False,
                    "stale": False,
                    "age_seconds": 0,
                    "refresh_seconds": refresh_seconds,
                    "fallback": True,
                }
                return payload, HTTPStatus.OK
            payload = {
                "activity": {},
                "agents": [],
                "agent_windows": {},
                "errors": [],
                "session_scope": "configured",
                "session_file_hours": bounded_hours,
                "cache": {
                    "hit": False,
                    "stale": True,
                    "age_seconds": None,
                    "refresh_seconds": refresh_seconds,
                    "refreshing_elsewhere": True,
                },
            }
            return payload, HTTPStatus.OK
        payload = self.build_activity_payload(hours=bounded_hours)
        self.set_tabber_activity_cache(payload, source_signature=source_signature)
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
    ) -> tuple[SessionFilesPayload, HTTPStatus]:
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
        payloads: dict[str, SessionFilesPayload] = {}
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

        def load_session_payload(name: str, info: SessionInfo) -> tuple[SessionFilesPayload, HTTPStatus]:
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
        if action == "background_release_owner":
            requester = request.get("requester")
            return self.background_release_owner(requester if isinstance(requester, dict) else {})
        if action == "background_status":
            payload, _status = self.background_owner_status_payload()
            return {"ok": True, "status": payload}
        if action == "background_ping":
            return {"ok": True, "status": self.background_owner.status_payload()}
        if action == "background_client_event":
            return self.handle_background_client_event(request)
        if action == "background_refresh":
            role = str(request.get("role") or "")
            payload = request.get("payload") if isinstance(request, dict) else {}
            self.request_background_refresh(role, payload if isinstance(payload, dict) else {})
            return {"ok": True, "accepted": True, "role": role}
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

    def build_session_metadata_payload(self, lightweight: bool = False) -> dict[str, Any]:
        refresh_errors = self.refresh_sessions(maintenance=not lightweight)
        sessions, errors = discover_sessions(self.sessions)
        with metadata_build_cache():
            session_payloads = {
                name: session_to_json(info, self.metadata_cache, allow_network=False, include_metadata=not lightweight)
                for name, info in sessions.items()
            }
            indexed_repos = [] if lightweight else indexed_repo_summaries(cache=self.metadata_cache, allow_network=False)
        agent_payload = {"agentAuth": {}, "availableAgents": available_agent_commands()} if lightweight else self.agent_auth_payload()
        payload = {
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "server_version": YOLOMUX_VERSION,
            "client_revision": yolomux_client_revision(),
            "server_started_at": SERVER_STARTED_AT,
            "server_uptime_seconds": max(0.0, time.time() - SERVER_STARTED_AT),
            "session_order": self.sessions,
            "sessions": session_payloads,
            "indexed_repos": indexed_repos,
            # refresh agent login status on the metadata poll (cached server-side) so the
            # new-session picker re-enables an agent within the cache TTL after the user logs in.
            "agentAuth": agent_payload["agentAuth"],
            "availableAgents": agent_payload["availableAgents"],
            "errors": [*refresh_errors, *errors],
            "metadata_loading": lightweight,
        }
        if not lightweight:
            self.apply_metadata_badge_pulses(session_payloads)
            self.warm_metadata_cache_async(sessions)
        return payload

    def build_transcripts_payload(self, lightweight: bool = False) -> dict[str, Any]:
        return self.build_session_metadata_payload(lightweight=lightweight)

    def agent_auth_payload(self, force: bool = False) -> dict[str, Any]:
        return {
            "agentAuth": agent_auth_status(force=True) if force else cached_agent_auth_status_snapshot(),
            "availableAgents": available_agent_commands(),
        }

    def session_metadata_payload(self, force: bool = False) -> dict[str, Any]:
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
        if not force:
            payload = self.build_session_metadata_payload(lightweight=True)
            payload["cache"] = {
                "hit": False,
                "stale": True,
                "age_seconds": 0,
                "refresh_seconds": max_age,
                "refreshing": self.start_transcripts_payload_refresh(publish=True, defer=True),
                "lightweight": True,
            }
            return payload
        payload = self.build_session_metadata_payload()
        self.set_transcripts_payload_cache(payload)
        payload["cache"] = {
            "hit": False,
            "stale": False,
            "age_seconds": 0,
            "refresh_seconds": max_age,
            "refreshing": False,
        }
        return payload

    def transcripts_payload(self, force: bool = False) -> dict[str, Any]:
        return self.session_metadata_payload(force=force)

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
        git_data = project_git(project)
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
        git_data = project_git(project)
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
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "metadata-warm"})
            return
        with self.metadata_warm_lock:
            if self.metadata_warm_running:
                return
            self.metadata_warm_running = True
        snapshot = dict(sessions)
        worker = threading.Thread(target=self.warm_metadata_cache, args=(snapshot,), daemon=True)
        worker.start()

    def warm_metadata_cache(self, sessions: dict[str, SessionInfo]) -> None:
        try:
            with metadata_build_cache():
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
        self.switch_attached_tmux_clients(session, target)
        return {"session": session, "window": window_text, "ok": True}, HTTPStatus.OK

    def switch_attached_tmux_clients(self, session: str, target: str) -> int:
        switched = 0
        for row in tmux_session_client_rows(session):
            client_name = str(row.get("name") or "").strip()
            if not client_name:
                continue
            result = tmux(["switch-client", "-c", client_name, "-t", target], timeout=1.0)
            if result.returncode == 0:
                switched += 1
                continue
            logger.debug("tmux switch-client failed for %s -> %s: %s", client_name, target, cmd_error(result, "tmux switch-client failed"))
        return switched

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

    @requires_known_session(refresh=True, maintenance=False)
    def ensure_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        if tmux_has_exact_session(session):
            return {"session": session, "created": False, "ok": True}, HTTPStatus.OK

        self.sessions = [item for item in self.sessions if item != session]
        return {"error": f"session no longer exists: {session}"}, HTTPStatus.NOT_FOUND

    def tmux_session_exists_payload(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        clean_session = str(session or "").strip()
        if not clean_session:
            return {"error": "session is required", "exists": False}, HTTPStatus.BAD_REQUEST
        sessions, error = list_tmux_session_names()
        if error is not None:
            return {"session": clean_session, "exists": None, "error": error}, HTTPStatus.SERVICE_UNAVAILABLE
        self.sessions = sessions
        return {"session": clean_session, "exists": clean_session in sessions, "ok": True}, HTTPStatus.OK

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
        color = self.tmux_theme_color or tmux_theme_color_from_settings(settings_payload().get("settings", {}))
        theme_result = apply_tmux_theme_color_to_new_session(session, color, runner=tmux)
        self.tmux_theme_color = color
        if theme_result.get("errors"):
            logger.debug("tmux theme apply failed for new session %s: %s", session, theme_result.get("errors"))
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

    def auto_approve_agent_targets(
        self,
        session: str,
        payload: dict[str, Any] | None = None,
        discovered_sessions: dict[str, SessionInfo] | None = None,
    ) -> list[str]:
        targets: list[str] = []
        seen: set[str] = set()

        def add_target(value: Any) -> None:
            target = str(value or "").strip()
            if not target or target in seen:
                return
            seen.add(target)
            targets.append(target)

        info = discovered_sessions.get(session) if discovered_sessions is not None else None
        if info is None and discovered_sessions is None:
            discovered, _errors = discover_sessions([session])
            info = discovered.get(session)
        if info is not None:
            for agent in info.agents:
                if str(agent.kind or "").lower() not in {"claude", "codex"}:
                    continue
                add_target(agent.pane_target)

        signal_payload = payload if payload is not None else self.tmux_signal_snapshot()
        agents = signal_payload.get("agents") if isinstance(signal_payload, dict) else None
        if not isinstance(agents, list):
            return targets
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            if str(agent.get("session") or "") != session or agent.get("dead") is True:
                continue
            add_target(agent.get("target") or agent.get("pane_id"))
        return targets

    def auto_approve_session_lock_owner(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
    ) -> dict[str, Any] | None:
        """The owner of session's YO lock when another server holds it, else None.

        YO workers lock per agent-pane target (auto_approve_agent_targets), NOT the bare session,
        so a server without a local worker must probe those pane-target locks to notice another
        server's ownership. The bare session is probed too, covering the no-agent fallback path and
        any legacy session-named lock. Checking only the session lock missed every agent-backed
        session, which is what silently dropped the cross-server "YO running elsewhere" (yellow)
        marker on the other servers.
        """
        targets = self.auto_approve_agent_targets(session, discovered_sessions=discovered_sessions) or [session]
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
        if not capture_pane and not capture_roster_target and not capture_idle_bare_session and not self.auto_approve_session_has_pending_prompt(session) and not self.auto_approve_capture_allowed_for_target(target):
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

    @staticmethod
    def agent_transcript_id(agent: AgentInfo) -> str:
        session_id = str(agent.session_id or "").strip()
        if session_id:
            return session_id
        transcript = str(agent.transcript or "").strip()
        return Path(transcript).stem if transcript else ""

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

    @staticmethod
    def attention_ack_key(*parts: Any) -> str:
        return json.dumps([str(part or "") for part in parts], separators=(",", ":"))

    @staticmethod
    def prompt_attention_signature(prompt: dict[str, Any] | None, screen: dict[str, Any] | None) -> str:
        prompt_payload = prompt if isinstance(prompt, dict) else {}
        screen_payload = screen if isinstance(screen, dict) else {}
        if prompt_payload.get("visible") is True:
            for key in ("signature", "hash", "question_text", "text", "command"):
                value = str(prompt_payload.get(key) or "").strip()
                if value:
                    return value
        if str(screen_payload.get("key") or "") in {"approval", "needs-approval", "needs-input"}:
            for key in ("signature", "hash", "question_text", "text", "key"):
                value = str(screen_payload.get(key) or "").strip()
                if value:
                    return value
        return ""

    def prompt_attention_key(self, session: str, prompt: dict[str, Any] | None, screen: dict[str, Any] | None) -> str:
        signature = self.prompt_attention_signature(prompt, screen)
        return self.attention_ack_key("prompt", session, signature) if signature else ""

    @staticmethod
    def agent_window_attention_signature(state: str, screen: dict[str, Any] | None, stopped_ts: float = 0.0) -> str:
        if state == "cooldown":
            return str(stopped_ts) if stopped_ts > 0 else ""
        if state not in {"approval", "needs-approval", "needs-input", "interrupted"}:
            return ""
        screen_payload = screen if isinstance(screen, dict) else {}
        for key in ("signature", "hash", "question_text", "text", "key"):
            value = str(screen_payload.get(key) or "").strip()
            if value:
                return value
        return state

    def agent_window_attention_key(self, session: str, window: str, pane_target: str, kind: str, state: str, signature: str) -> str:
        if not signature:
            return ""
        return self.attention_ack_key("agent-window", session, self.agent_window_index_key(window), pane_target, kind, state, signature)

    def prune_attention_ack_keys_locked(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        for key, ts in list(self.attention_ack_keys.items()):
            if current - ts > ATTENTION_ACK_TTL_SECONDS:
                self.attention_ack_keys.pop(key, None)
        while len(self.attention_ack_keys) > ATTENTION_ACK_MAX_KEYS:
            oldest = min(self.attention_ack_keys, key=lambda item: self.attention_ack_keys[item])
            self.attention_ack_keys.pop(oldest, None)

    def attention_acknowledged(self, key: str) -> bool:
        if not key:
            return False
        with self.attention_ack_lock:
            self.prune_attention_ack_keys_locked()
            return key in self.attention_ack_keys

    def attention_ack_payload_for_session(self, session: str) -> dict[str, Any]:
        with self.attention_ack_lock:
            self.prune_attention_ack_keys_locked()
            keys = []
            for key in self.attention_ack_keys:
                try:
                    parts = json.loads(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(parts, list) and len(parts) >= 2 and str(parts[1]) == str(session):
                    keys.append(key)
        return {"keys": sorted(keys)}

    def invalidate_auto_approve_cache(self) -> None:
        with self.auto_approve_cache_condition:
            self.auto_approve_cache = None
            self.auto_approve_cache_refreshing = False
            self.auto_approve_cache_condition.notify_all()

    def _read_shared_attention_acks_locked(self) -> tuple[dict[str, float], int]:
        data = self._read_shared_tmux_ai_status_locked()
        attention = data.get("attention_acks") if isinstance(data.get("attention_acks"), dict) else {}
        raw_keys = attention.get("keys") if isinstance(attention.get("keys"), dict) else {}
        keys: dict[str, float] = {}
        if isinstance(raw_keys, dict):
            for raw_key, raw_ts in raw_keys.items():
                key = str(raw_key or "").strip()
                try:
                    ts = float(raw_ts)
                except (TypeError, ValueError):
                    continue
                if key and ts > 0:
                    keys[key] = ts
        try:
            rev = int(attention.get("rev", 0)) if isinstance(attention, dict) else 0
        except (TypeError, ValueError):
            rev = 0
        return keys, max(0, rev)

    def _prune_attention_ack_dict(self, keys: dict[str, float], now: float) -> None:
        for key, ts in list(keys.items()):
            if now - ts > ATTENTION_ACK_TTL_SECONDS:
                keys.pop(key, None)
        while len(keys) > ATTENTION_ACK_MAX_KEYS:
            keys.pop(min(keys, key=lambda item: keys[item]), None)

    def write_shared_attention_acks_union(self, local_keys: dict[str, float]) -> int:
        now = time.time()
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            attention = status.get("attention_acks") if isinstance(status.get("attention_acks"), dict) else {}
            merged, rev = self._read_shared_attention_acks_locked()
            for key, ts in local_keys.items():
                if key and ts > 0:
                    merged[key] = max(ts, merged.get(key, 0.0))
            self._prune_attention_ack_dict(merged, now)
            rev += 1
            status["attention_acks"] = {
                "rev": rev,
                "updated_at": now,
                "keys": merged,
                "writer": self.background_owner.owner_payload(),
                **({"legacy_rev": attention.get("legacy_rev")} if isinstance(attention, dict) and attention.get("legacy_rev") else {}),
            }
            self._write_shared_tmux_ai_status_locked(status)
        with self.attention_ack_lock:
            self.attention_ack_keys = dict(merged)
        with self.client_watch_lock:
            self.client_watch_attention_ack_rev = rev
        return rev

    def merge_shared_attention_acks(self) -> bool:
        # Hold file_lock across the whole read->rev-check->apply. write_shared_attention_acks_union
        # holds file_lock for its entire read-modify-write plus its in-memory cache + rev update, so
        # keeping the lock here makes the two mutually exclusive. Releasing it before the apply let a
        # concurrent local ack interleave: this poll would then regress client_watch_attention_ack_rev
        # and overwrite attention_ack_keys with the stale snapshot it read earlier, dropping a just-acked
        # key from the cache. The guard is monotonic (<=) so a stale or equal rev is never applied, and
        # changed compares the key set so a timestamp-only re-ack does not trigger a client refetch.
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            file_keys, rev = self._read_shared_attention_acks_locked()
            with self.client_watch_lock:
                if rev <= self.client_watch_attention_ack_rev:
                    return False
                self.client_watch_attention_ack_rev = rev
            with self.attention_ack_lock:
                changed = set(self.attention_ack_keys) != set(file_keys)
                self.attention_ack_keys = dict(file_keys)
        return changed

    def poll_attention_acks_client_event_once(self) -> list[str]:
        if not self.merge_shared_attention_acks():
            return []
        self.invalidate_auto_approve_cache()
        self.publish_client_event(
            "auto_approve_changed",
            {"refresh": True, "trigger": "attention_ack_sync"},
            trigger="timer",
            cache="ready",
        )
        return ["auto_approve_changed"]

    def acknowledge_attention(self, payload: dict[str, Any] | None) -> tuple[dict[str, Any], HTTPStatus]:
        source = payload if isinstance(payload, dict) else {}
        raw_keys = source.get("keys") if isinstance(source.get("keys"), list) else [source.get("key")]
        keys: list[str] = []
        for raw in raw_keys:
            key = str(raw or "").strip()
            if not key or len(key) > 512 or key in keys:
                continue
            keys.append(key)
        if not keys:
            return {"error": "attention acknowledgement keys required"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        with self.attention_ack_lock:
            for key in keys:
                self.attention_ack_keys[key] = now
            self.prune_attention_ack_keys_locked(now)
        self.write_shared_attention_acks_union({key: now for key in keys})
        self.notify_background_client_event_followers(
            "attention_acks_changed",
            {},
            self.shared_background_client_event_record("attention_acks_changed", {}),
        )
        self.invalidate_auto_approve_cache()
        auto_payload, status = self.auto_approve_status()
        if status == HTTPStatus.OK and isinstance(auto_payload, dict):
            self.publish_client_event(
                "auto_approve_changed",
                {"status": int(status), "data": auto_payload},
                trigger="attention_ack",
                cache="ready",
            )
        return {"ok": True, "acknowledged": keys, "auto_approve": auto_payload, "status": int(status)}, HTTPStatus.OK

    @staticmethod
    def agent_window_index_key(value: Any) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            text = str(value or "").strip()
            return text
        return str(number)

    @staticmethod
    def agent_window_path_match(raw: dict[str, Any], window: str, kind: str) -> bool:
        raw_window = TmuxWebtermApp.agent_window_index_key(raw.get("window_index") if raw.get("window_index") is not None else raw.get("window"))
        if raw_window != TmuxWebtermApp.agent_window_index_key(window):
            return False
        raw_kind = str(raw.get("kind") or "").strip().lower()
        return not raw_kind or not kind or raw_kind == kind

    @staticmethod
    def normalized_agent_window_repo_path(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(Path(text).expanduser().resolve(strict=False))
        except OSError:
            return str(Path(text).expanduser())

    def agent_window_git_inventory(self, path: str, cache: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
        root = self.normalized_agent_window_repo_path(path)
        if not root:
            return None
        if root not in cache:
            cache[root] = git_inventory(root)
        git_data = cache[root]
        if not isinstance(git_data, dict):
            return None
        return copy.deepcopy(git_data)

    def agent_window_path_records(
        self,
        info: SessionInfo,
        files_payload: dict[str, Any] | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        payload = files_payload if isinstance(files_payload, dict) else self.cached_session_files_payload_for_info(info)
        files = payload.get("files") if isinstance(payload, dict) else []
        git_cache: dict[str, dict[str, Any] | None] = {}
        records: dict[tuple[str, str], dict[str, Any]] = {}
        for file_item in files if isinstance(files, list) else []:
            if not isinstance(file_item, dict) or file_item.get("uploaded") is True:
                continue
            repo = self.normalized_agent_window_repo_path(file_item.get("repo"))
            if not repo or repo == "/":
                continue
            windows = file_item.get("agent_windows") if isinstance(file_item.get("agent_windows"), list) else []
            for raw_window in windows:
                if not isinstance(raw_window, dict):
                    continue
                window = self.agent_window_index_key(raw_window.get("window_index") if raw_window.get("window_index") is not None else raw_window.get("window"))
                kind = str(raw_window.get("kind") or "").strip().lower()
                if not window or kind not in {"claude", "codex"}:
                    continue
                key = (window, kind)
                item = records.setdefault(key, {"paths_by_root": {}})
                paths_by_root = item["paths_by_root"]
                path_record = paths_by_root.setdefault(repo, {"path": repo, "mtime": 0.0})
                path_record["mtime"] = max(self.float_value(path_record.get("mtime"), 0.0), self.float_value(file_item.get("mtime"), 0.0))
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for key, item in records.items():
            paths = sorted(item.get("paths_by_root", {}).values(), key=lambda row: (-self.float_value(row.get("mtime"), 0.0), str(row.get("path") or "")))
            for path_item in paths:
                git_data = self.agent_window_git_inventory(str(path_item.get("path") or ""), git_cache)
                if git_data is not None:
                    path_item["git"] = git_data
            result[key] = {
                "path_entries": paths,
                "paths": [str(path_item.get("path") or "") for path_item in paths if str(path_item.get("path") or "")],
                "git": copy.deepcopy(paths[0].get("git")) if paths and isinstance(paths[0].get("git"), dict) else None,
            }
        return result

    @staticmethod
    def agent_window_pane_maps(info: SessionInfo) -> tuple[dict[str, bool], dict[str, PaneInfo]]:
        current_by_window: dict[str, bool] = {}
        pane_by_window: dict[str, PaneInfo] = {}
        for pane in info.panes:
            window = TmuxWebtermApp.agent_window_index_key(pane.window)
            if not window:
                continue
            current_by_window[window] = current_by_window.get(window, False) or pane.window_active is True
            current = pane_by_window.get(window)
            if current is None or (pane.active and not current.active) or (pane.window_active and not current.window_active):
                pane_by_window[window] = pane
        return current_by_window, pane_by_window

    def agent_window_fallback_path_record(self, pane: PaneInfo | None, git_cache: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
        path = self.normalized_agent_window_repo_path(pane.current_path if pane else "")
        if not path:
            return {"path": "", "paths": [], "path_entries": [], "git": None}
        git_data = self.agent_window_git_inventory(path, git_cache)
        if isinstance(git_data, dict) and git_data.get("root"):
            root = self.normalized_agent_window_repo_path(git_data.get("root"))
            entry = {"path": root, "mtime": 0.0, "git": git_data}
            return {"path": root, "paths": [root], "path_entries": [entry], "git": git_data}
        return {"path": path, "paths": [], "path_entries": [], "git": None}

    def agent_window_status_payloads(
        self,
        session: str,
        *,
        info: SessionInfo | None = None,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        activity_snapshot: dict[str, Any] | None = None,
        preclassified_by_target: dict[str, dict[str, Any]] | None = None,
        files_payload: dict[str, Any] | None = None,
        include_path_metadata: bool = True,
    ) -> list[dict[str, Any]]:
        if info is None:
            info = discovered_sessions.get(session) if discovered_sessions is not None else None
        if info is None:
            infos, _errors = discover_sessions([session])
            info = infos.get(session)
        if info is None:
            return []
        activity = self.activity_snapshot_with_recency(activity_snapshot)
        observed_ts = time.time()
        rows: list[dict[str, Any]] = []
        window_names = {str(pane.window or ""): str(pane.window_name or "") for pane in info.panes}
        path_records = self.agent_window_path_records(info, files_payload=files_payload) if include_path_metadata else {}
        current_by_window, pane_by_window = self.agent_window_pane_maps(info)
        fallback_git_cache: dict[str, dict[str, Any] | None] = {}
        for agent_index, agent in enumerate(info.agents):
            kind = str(agent.kind or "").lower()
            if kind not in {"claude", "codex"}:
                continue
            window, pane = session_files.agent_window_for_info(info, agent)
            screen = self.agent_window_screen_state(agent, preclassified_by_target=preclassified_by_target)
            state = self.agent_window_state_from_screen(screen)
            elapsed = self.float_value(screen.get("display_elapsed_seconds"), self.float_value(screen.get("status_elapsed_seconds"), -1.0))
            last_active_ts = self.agent_window_last_active_ts(activity, session, window)
            working_stopped_ts = last_active_ts if state == "idle" and last_active_ts > 0 else 0.0
            window_index: int | None
            try:
                window_index = int(window)
            except ValueError:
                window_index = None
            window_name = window_names.get(window) or kind
            window_label = f"{window}:{kind}" if window else kind
            pane_record = pane_by_window.get(self.agent_window_index_key(window))
            pid = int(pane_record.process_label_pid or pane_record.pid) if pane_record and (pane_record.process_label_pid or pane_record.pid) else int(agent.pid or 0)
            window_is_current = current_by_window.get(self.agent_window_index_key(window), False)
            path_record = path_records.get((self.agent_window_index_key(window), kind))
            if not path_record and include_path_metadata:
                path_record = self.agent_window_fallback_path_record(pane_record, fallback_git_cache)
            if not path_record:
                path_record = {"path": "", "paths": [], "path_entries": [], "git": None}
            path_entries = copy.deepcopy(path_record.get("path_entries") if isinstance(path_record, dict) else [])
            paths = [str(item.get("path") or "") for item in path_entries if isinstance(item, dict) and str(item.get("path") or "")]
            fallback_path = str(path_record.get("path") or "") if isinstance(path_record, dict) else ""
            attention_signature = self.agent_window_attention_signature(state, screen)
            attention_key = self.agent_window_attention_key(session, window, str(agent.pane_target or ""), kind, state, attention_signature)
            cooldown_signature = self.agent_window_attention_signature("cooldown", screen, working_stopped_ts)
            cooldown_attention_key = self.agent_window_attention_key(session, window, str(agent.pane_target or ""), kind, "cooldown", cooldown_signature)
            row = {
                "kind": kind,
                "state": state,
                "window": window,
                "window_index": window_index,
                "window_name": window_name,
                "label": window_label,
                "window_label": window_label,
                "pane": pane,
                "pane_target": str(agent.pane_target or ""),
                "pid": pid if pid > 0 else None,
                "current": window_is_current,
                "window_active": window_is_current,
                "path": paths[0] if paths else fallback_path,
                "paths": paths,
                "path_entries": path_entries,
                "git": copy.deepcopy(path_record.get("git")) if isinstance(path_record, dict) and isinstance(path_record.get("git"), dict) else None,
                "transcript": str(agent.transcript or ""),
                "transcript_id": self.agent_transcript_id(agent),
                "agent_session_id": str(agent.session_id or ""),
                "working_elapsed_seconds": elapsed if state == "working" and elapsed >= 0 else None,
                "idle_since": last_active_ts if state == "idle" and last_active_ts > 0 else None,
                "last_active_ts": last_active_ts,
                "working_stopped_ts": working_stopped_ts if working_stopped_ts > 0 else None,
                "observed_ts": observed_ts,
                "screen_text": str(screen.get("text") or ""),
                "status_tokens": screen.get("status_tokens") if isinstance(screen.get("status_tokens"), (int, float)) else None,
                "_agent_order": agent_index,
            }
            if attention_key:
                row["attention_key"] = attention_key
                row["attention_acknowledged"] = self.attention_acknowledged(attention_key)
            if cooldown_attention_key:
                row["cooldown_attention_key"] = cooldown_attention_key
                row["cooldown_acknowledged"] = self.attention_acknowledged(cooldown_attention_key)
            rows.append(row)
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
        timings: dict[str, float] | None = None,
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
            owner = self.auto_approve_session_lock_owner(session, discovered_sessions=discovered_sessions)
            if owner:
                payload.update({
                    "enabled_elsewhere": True,
                    "locked": True,
                    "lock_owner": owner,
                    "last_action": auto_approve_lock_message(owner),
                    "error": auto_approve_lock_message(owner),
                })
        capture_target = self.auto_approve_capture_target(session, discovered_sessions=discovered_sessions)
        prompt_started = time.perf_counter()
        prompt, screen = self.prompt_and_screen_status(
            session,
            discovered_sessions=discovered_sessions,
            capture_pane=include_live_prompt,
            capture_bare_session_when_roster=capture_bare_session_when_roster,
        )
        add_phase_timing(timings, "prompt_screen", prompt_started)
        prompt_attention_key = self.prompt_attention_key(session, prompt, screen)
        if prompt_attention_key:
            prompt["attention_key"] = prompt_attention_key
            prompt["attention_acknowledged"] = self.attention_acknowledged(prompt_attention_key)
        payload["prompt"] = prompt
        payload["screen"] = screen
        payload["attention_acks"] = self.attention_ack_payload_for_session(session)
        if prompt_attention_key:
            payload["prompt_attention_key"] = prompt_attention_key
            payload["prompt_attention_acknowledged"] = self.attention_acknowledged(prompt_attention_key)
        info = discovered_sessions.get(session) if discovered_sessions is not None else None
        agent_windows_started = time.perf_counter()
        payload["agent_windows"] = self.agent_window_status_payloads(
            session,
            info=info,
            discovered_sessions=discovered_sessions,
            activity_snapshot=activity_snapshot,
            preclassified_by_target={capture_target: screen} if capture_target else None,
            include_path_metadata=False,
        )
        add_phase_timing(timings, "agent_windows", agent_windows_started)
        return payload

    def build_auto_approve_status(
        self,
        session: str | None = None,
        timings: dict[str, float] | None = None,
    ) -> tuple[AutoApproveState | AutoApproveStatusPayload, HTTPStatus]:
        refresh_started = time.perf_counter()
        refresh_errors = self.refresh_sessions(maintenance=False)
        add_phase_timing(timings, "refresh_sessions", refresh_started)
        if session is not None and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        removed = False
        worker_started = time.perf_counter()
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
        add_phase_timing(timings, "worker_sync", worker_started)
        if removed:
            self.persist_auto_sessions()
        activity_snapshot = self.activity_snapshot_with_recency()
        if session is not None:
            payload = self.auto_approve_session_status(session, activity_snapshot=activity_snapshot, timings=timings)
            if timings:
                payload["timings"] = dict(timings)
            return payload, HTTPStatus.OK
        discover_started = time.perf_counter()
        discovered_sessions, discovery_errors = discover_sessions(self.sessions)
        add_phase_timing(timings, "discover_sessions", discover_started)
        sessions_started = time.perf_counter()
        sessions_payload = {
            name: self.auto_approve_session_status(
                name,
                discovered_sessions=discovered_sessions,
                include_live_prompt=False,
                capture_bare_session_when_roster=True,
                activity_snapshot=activity_snapshot,
                timings=timings,
            )
            for name in self.sessions
        }
        add_phase_timing(timings, "sessions", sessions_started)
        payload: AutoApproveStatusPayload = {
            "session_order": self.sessions,
            "sessions": sessions_payload,
            "errors": [*refresh_errors, *discovery_errors],
            "rules": self.yolo_rules_payload(),
        }
        if timings:
            payload["timings"] = dict(timings)
        return payload, HTTPStatus.OK

    def auto_approve_cache_payload(self, cached: tuple[float, tuple[AutoApproveStatusPayload, HTTPStatus]]) -> tuple[AutoApproveStatusPayload, HTTPStatus]:
        stored_at, (payload, status) = cached
        age_seconds = max(0.0, time.monotonic() - stored_at)
        result = copy.deepcopy(payload)
        result["cache"] = {
            "age_seconds": round(age_seconds, 3),
            "max_age_seconds": AUTO_APPROVE_CACHE_MAX_AGE_SECONDS,
            "refreshing": self.auto_approve_cache_refreshing,
            "stale": age_seconds > AUTO_APPROVE_CACHE_MAX_AGE_SECONDS,
        }
        return result, status

    def set_auto_approve_cache(self, payload: AutoApproveStatusPayload, status: HTTPStatus) -> None:
        with self.auto_approve_cache_condition:
            self.auto_approve_cache = (time.monotonic(), (copy.deepcopy(payload), status))
            self.auto_approve_cache_refreshing = False
            self.auto_approve_cache_condition.notify_all()

    def run_auto_approve_cache_refresh(self) -> None:
        try:
            timings: dict[str, float] = {}
            payload, status = self.build_auto_approve_status(timings=timings)
            if isinstance(payload, dict):
                payload["timings"] = dict(timings)
            self.set_auto_approve_cache(payload, status)
        except Exception:
            logger.exception("auto-approve cache refresh failed")
            with self.auto_approve_cache_condition:
                self.auto_approve_cache_refreshing = False
                self.auto_approve_cache_condition.notify_all()

    def start_auto_approve_cache_refresh(self) -> bool:
        with self.auto_approve_cache_condition:
            if self.auto_approve_cache_refreshing:
                return False
            self.auto_approve_cache_refreshing = True
        worker = threading.Thread(target=self.run_auto_approve_cache_refresh, name="auto-approve-cache-refresh", daemon=True)
        worker.start()
        return True

    def refresh_auto_approve_cache_sync(self) -> tuple[AutoApproveStatusPayload, HTTPStatus]:
        with self.auto_approve_cache_condition:
            if self.auto_approve_cache_refreshing:
                while self.auto_approve_cache_refreshing and self.auto_approve_cache is None:
                    self.auto_approve_cache_condition.wait(timeout=0.5)
                if self.auto_approve_cache is not None:
                    return self.auto_approve_cache_payload(self.auto_approve_cache)
            self.auto_approve_cache_refreshing = True
        try:
            timings: dict[str, float] = {}
            payload, status = self.build_auto_approve_status(timings=timings)
            if isinstance(payload, dict):
                payload["timings"] = dict(timings)
            self.set_auto_approve_cache(payload, status)
            with self.auto_approve_cache_condition:
                assert self.auto_approve_cache is not None
                return self.auto_approve_cache_payload(self.auto_approve_cache)
        except Exception:
            with self.auto_approve_cache_condition:
                self.auto_approve_cache_refreshing = False
                self.auto_approve_cache_condition.notify_all()
            raise

    def auto_approve_status(self, session: str | None = None) -> tuple[AutoApproveState | AutoApproveStatusPayload, HTTPStatus]:
        if session is not None:
            timings: dict[str, float] = {}
            return self.build_auto_approve_status(session, timings=timings)
        with self.auto_approve_cache_condition:
            cached = self.auto_approve_cache
            if cached is not None:
                stored_at, _payload = cached
                age_seconds = max(0.0, time.monotonic() - stored_at)
                if age_seconds > AUTO_APPROVE_CACHE_MAX_AGE_SECONDS:
                    self.start_auto_approve_cache_refresh()
                return self.auto_approve_cache_payload(cached)
        return self.refresh_auto_approve_cache_sync()

    def stop_auto_approve_all(self) -> None:
        with self.auto_workers_lock:
            for worker in list(self.auto_workers.values()):
                worker.stop()
            self.auto_workers.clear()
            self.auto_worker_session_map().clear()
        self.background_owner.stop()
        self.close_yoagent_codex_app_server()
        self.control_server.stop()

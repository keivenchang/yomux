from __future__ import annotations

import collections
import copy
import ctypes
import hashlib
import hmac
import json
import logging
import math
import os
import plistlib
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
from concurrent.futures import Future
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from functools import wraps
from http import HTTPStatus
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Mapping
from urllib.parse import unquote
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

try:
    from watchfiles import watch as watchfiles_watch
except ImportError:  # pragma: no cover - direct-source fallback before setup
    watchfiles_watch = None

from . import common
from . import file_index
from . import filesystem
from . import session_files
from .stats_current import resolution as stats_resolution
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
from .approvald import ApprovalClient
from .auto_approve_worker import auto_approve_lock_message
from .auto_approve_worker import auto_approve_lock_message_fields
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
from .locales import LANGUAGE_PREFERENCES
from .login_escalation import EdgeBlockController
from .login_escalation import default_edge_runner
from .login_rate_limit import LOGIN_THROTTLE_DATABASE_NAME
from .login_rate_limit import LOGIN_THROTTLE_OVERRIDE_NAME
from .login_rate_limit import LoginRateLimiter
from .login_rate_limit import load_login_rate_policy
from .locales import message_descriptor
from .locales import message_fields
from .locales import normalize_locale
from .locales import user_message_payload
from .common import as_dict
from .common import next_numbered_session_name
from .common import positive_finite_number
from .common import tail_file_lines
from .common import truncate_text
from .common import yolomux_client_revision
from .control import YolomuxControlServer
from .control import send_yolomux_control_request
from .search_indexer import SearchIndexerClient
from .jobd import JobClient
from .pricing_catalog import PricingCatalog
from .pricing_catalog import PricingRefreshCoordinator
from .stats_current.client import StatsCurrentClient
from .stats_current.client import iter_append_batches as stats_current_append_batches
from .stats_current import collectors as stats_current_collectors
from .stats_current.http import StatsHttpForwarder
from .stats_current import observations as stats_current_observations
from .stats_current import protocol as stats_current_protocol
from .stats_current import revision as stats_current_revision
from .stats_current.runtime import StatsCurrentRuntime
from .stats_current import storage as stats_current_storage
from .stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from .stats_current import usage as stats_current_usage
from .drop_actions import run_drop_action
from .events import EventLog
from .events import RunHistoryStore
from .events import mutate_yolomux_state
from .events import search_snippet
from .events import read_yolomux_state
from .events import update_yolomux_state
from .server_logs import emit_server_log
from .agent_tui import classify_agent_pane
from .agent_tui import normalized_prompt_state
from .chat_store import ChatStore
from .chat_service import CHAT_YOAGENT_INSTANCE_ID
from .chat_service import CHAT_YOAGENT_USERNAME
from .chat_service import ChatService
from .chat_store import CHAT_TYPING_LEASE_SECONDS
from .metadata import MetadataCache
from .metadata import github_checks_unknown
from .metadata import git_inventory
from .metadata import indexed_repo_summaries
from .metadata import INDEXED_REPO_ROOTS_CACHE_SECONDS
from .metadata import metadata_build_cache
from .metadata import project_inventory
from .metadata import pull_request_number_from_subject
from .metadata import activity_work_summary_from_graph
from .metadata import session_work_graph
from .metadata import session_to_json
from .metadata import watched_pr_metadata
from .sessions import active_window_for_panes
from .sessions import discover_sessions
from .sessions import discover_status_sessions
from .statusd_client import StatusClient
from .statusd_protocol import StatusProtocolError
from .statusd_protocol import validate_snapshot as validate_status_snapshot
from .settings import default_settings
from .settings import save_settings
from .settings import SETTINGS_PATH
from .settings import settings_payload
from .settings import summary_settings as normalized_summary_settings
from .settings import usage_pricing_profile as configured_usage_pricing_profile
from .transcripts import codex_summary_prompt
from .transcripts import codex_event_text
from .transcripts import compact_summary_lines
from .transcripts import format_transcript_item
from .transcripts import transcript_activity_is_recent
from .transcripts import transcript_delta_result_state
from .transcripts import transcript_run_metadata
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
from .state_services import ActivityTranscriptService
from .state_services import ClientEventWatcherRecord
from .state_services import ClientWatchFileRecord
from .state_services import ClientWatchService
from .state_services import SessionFilesDiskPruneRecord
from .state_services import SessionFilesGitSnapshotRecord
from .state_services import SessionFilesService
from .state_services import SessionFilesWorkRecord
from .state_services import StatsCollectionState
from .state_services import TabberActivityWarmerRecord
from .uploads import sanitize_upload_filename
from .uploads import central_upload_target
from .uploads import UploadRetentionSweeper
from .uploads import UploadTargetError
from .uploads import unique_upload_path
from .web import bootstrap_agent_auth_status as cached_agent_auth_status_snapshot
from .web import server_string
from .workdir import agent_command
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_status
from .workdir import available_agent_commands
from .workdir import available_terminal_commands
from .workdir import terminal_command
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
from .yoagent.preferences import yoagent_user_message_text
from .yoagent.skills import delete_user_skill_file
from .yoagent.skills import list_user_skill_files
from .yoagent.skills import load_yoagent_skills
from .yoagent.skills import read_user_skill_file
from .yoagent.skills import skill_validation_payload
from .yoagent.skills import write_user_skill_file
from .yoagent.skills import YoagentSkillValidationError
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
from .yoagent.conversation import sanitized_stream_items as sanitized_yoagent_stream_items
from .yoagent.backends import yoagent_activity_payload_signature
from .yoagent.backends import yoagent_cli_auth_failure
from .yoagent.backends import yoagent_cli_fallback_reason
from .yoagent.backends import yoagent_language_directive
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
from .yoagent.session_summaries import YoagentSummaryWorkerRecord


logger = logging.getLogger(__name__)


ACTIVITY_SUMMARY_READY_PUSH_TRIGGERS = {"manual", "refresh", "force"}
METADATA_BADGES = ("main", "pr", "status", "ci")
METADATA_BADGE_SIGNATURES_STATE_KEY = "metadata_badge_signatures"
METADATA_BADGE_PULSE_UNTIL_STATE_KEY = "metadata_badge_pulse_until"


@dataclass
class MetadataBadgeRecord:
    signature: dict[str, str]
    pulse_until: dict[str, float]


@dataclass
class MetadataWarmRecord:
    worker: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)


@dataclass
class AutoApproveCacheRecord:
    payload: tuple[float, tuple[AutoApproveStatusPayload, HTTPStatus]] | None = None
    worker: object | None = None
    generation: int = 0
    # Agent-window state is classified while building the auto-approve roster.  Consumers
    # must be able to identify that immutable classification rather than independently
    # reclassifying the same tmux screen during the same refresh.
    agent_window_snapshot_revision: int = 0


@dataclass(frozen=True)
class AgentWindowAttentionInstance:
    cooldown_generation: int = 0
    cooldown_stopped_at: float = 0.0
    cooldown_idle_since: float = 0.0
    cooldown_cancelled_generation: int = 0
    cooldown_working: bool = False
    attention_generation: int = 0
    active_prompt_hash: str = ""

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> AgentWindowAttentionInstance:
        payload = record if isinstance(record, dict) else {}
        try:
            cooldown_generation = max(0, int(payload.get("cooldown_generation", 0)))
        except (TypeError, ValueError):
            cooldown_generation = 0
        try:
            cooldown_stopped_at = max(0.0, float(payload.get("cooldown_stopped_at", 0.0)))
        except (TypeError, ValueError):
            cooldown_stopped_at = 0.0
        try:
            cooldown_idle_since = max(0.0, float(payload.get("cooldown_idle_since", 0.0)))
        except (TypeError, ValueError):
            cooldown_idle_since = 0.0
        try:
            cooldown_cancelled_generation = max(0, int(payload.get("cooldown_cancelled_generation", 0)))
        except (TypeError, ValueError):
            cooldown_cancelled_generation = 0
        try:
            attention_generation = max(0, int(payload.get("attention_generation", 0)))
        except (TypeError, ValueError):
            attention_generation = 0
        return cls(
            cooldown_generation=cooldown_generation,
            cooldown_stopped_at=cooldown_stopped_at,
            cooldown_idle_since=cooldown_idle_since,
            cooldown_cancelled_generation=cooldown_cancelled_generation,
            cooldown_working=payload.get("cooldown_working") is True,
            attention_generation=attention_generation,
            active_prompt_hash=str(payload.get("active_prompt_hash") or ""),
        )

    def cooldown_state(self) -> tuple[int, float]:
        stopped_at = self.cooldown_stopped_at if self.cooldown_cancelled_generation < self.cooldown_generation else 0.0
        return self.cooldown_generation, stopped_at


@dataclass
class YoagentPrewarmRecord:
    prewarm_running: bool = False
    prewarm_status: dict[str, Any] = field(default_factory=dict)
    prewarm_worker: threading.Thread | None = None
    startup_generation: int = 0
    active_startup_generation: int | None = None
    reset_in_progress: bool = False


ATTENTION_ACK_MAX_KEYS = 4096
ATTENTION_ACK_TTL_SECONDS = 7 * 24 * 3600
ATTENTION_INSTANCE_MAX_ENTRIES = 2048
SESSION_FILES_CACHE_MAX_ITEMS = 64
SESSION_FILES_CACHE_SECONDS = 30.0
SESSION_FILES_CACHE_VERSION = 1
SESSION_FILES_CACHE_KEY_VERSION = 4
SESSION_FILES_CACHE_DIR = common.STATE_DIR / "session-files-cache"
SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
SESSION_FILES_DISK_CACHE_MAX_BYTES = 1024 * 1024 * 1024
SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS = 5 * 60
SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE = 256
SESSION_FILES_DISK_CACHE_INDEX_FILENAME = "cache-index.json"
SESSION_FILES_DISK_CACHE_INDEX_VERSION = 1
TABBER_ACTIVITY_CACHE_VERSION = 1
TABBER_ACTIVITY_CACHE_DIR = common.STATE_DIR / "activity-cache"
TABBER_ACTIVITY_CONSUMER_TTL_SECONDS = 30.0
# Session-files cold rebuilds parse transcripts and run Git at the same time.  Two
# workers was the best p95 on the captured eight-session shape; more workers only
# increase disk/GIL/subprocess contention.  Preferences may reduce or raise this
# within the deliberately small safe range.
SESSION_FILES_BATCH_MAX_WORKERS = 2
SESSION_FILES_GIT_SNAPSHOT_MAX_ITEMS = 128
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
# Stats can safely use the last agent-status snapshot while the existing asynchronous refresher
# collects the next one. This keeps collection from synchronously recapturing every tmux pane.
AUTO_APPROVE_STATS_CACHE_MAX_AGE_SECONDS = 15.0
SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS = SERVER_INTERACTIVE_EVENT_POLL_SECONDS
TMUX_SIGNAL_REMOVAL_EVENT_TTL_SECONDS = 10.0
INPUT_HEARTBEAT_COALESCE_SECONDS = 0.05
STATS_SAMPLE_CACHE_SECONDS = 0.95
STATS_AGENT_TOKEN_SAMPLE_SECONDS = 10.0
STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS = 60.0


def stats_current_usage_health(
    service_usage: Mapping[str, object],
    transcript_usage: Mapping[str, object],
    cadence_seconds: float,
    *,
    now: float | None = None,
) -> dict[str, object]:
    """Compare committed transcript growth with accepted usage atoms."""

    checked_at = time.time() if now is None else float(now)
    cadence = max(0.0, float(cadence_seconds))
    stale_bound = max(120.0, cadence * 3.0)
    last_visible = max(0.0, float(transcript_usage.get("last_visible_append_at") or 0.0))
    last_accepted = max(0.0, float(service_usage.get("last_accepted_at") or 0.0))
    visible_age = max(0.0, checked_at - last_visible) if last_visible > 0 else None
    accepted_age = max(0.0, checked_at - last_accepted) if last_accepted > 0 else None
    visibly_appending = visible_age is not None and visible_age <= stale_bound
    warning = visibly_appending and (
        last_accepted <= 0
        or (
            last_visible > last_accepted
            and accepted_age is not None
            and accepted_age > stale_bound
        )
    )
    return {
        "state": "warning" if warning else ("ok" if visibly_appending else "idle"),
        "reason": (
            "transcripts are advancing but usage atoms are stale"
            if warning
            else ("transcripts and usage atoms are advancing" if visibly_appending else "no recent transcript growth")
        ),
        "stale_bound_seconds": stale_bound,
        "visibly_appending": visibly_appending,
        "last_visible_append_age_seconds": visible_age,
        "last_accepted_atom_age_seconds": accepted_age,
    }
TMUX_AI_STATUS_VERSION = 1
STATS_HOST_RESOURCE_TIMEOUT_SECONDS = 0.75
_stats_host_fallback_warning_emitted = False
STATS_AGENT_ASK_STATES = frozenset({"approval", "needs-approval", "needs-input", "attention", "interrupted"})
STATS_AGENT_RUN_STATES = frozenset({"working"})
STATS_AGENT_TRANSITION_STATES = frozenset({"cooldown", "transition"})
# A terminal can briefly render an idle prompt while an agent is still producing its next update.
# Do not make that flicker a completed/yellow transition or a notification.
AGENT_WORKING_IDLE_CONFIRM_SECONDS = 5.0


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
        return current_darwin_system_cpu_times()
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


def darwin_sysctl_value(name: str, value_type: type[ctypes._SimpleCData]) -> int | None:
    """Read a scalar sysctl without spawning macOS's `sysctl` program."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        sysctlbyname = libc.sysctlbyname
        sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        sysctlbyname.restype = ctypes.c_int
        value = value_type()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if sysctlbyname(name.encode("utf-8"), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            return None
        return int(value.value)
    except (AttributeError, OSError):
        return None


def current_darwin_system_cpu_times() -> tuple[float, float] | None:
    """Read aggregate CPU ticks through Mach, avoiding `ps -A`."""
    if sys.platform != "darwin":
        return None
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libsystem.mach_host_self.restype = ctypes.c_uint32
        libsystem.mach_task_self.restype = ctypes.c_uint32
        libsystem.host_processor_info.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_uint32)]
        libsystem.host_processor_info.restype = ctypes.c_int
        libsystem.vm_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64]
        libsystem.vm_deallocate.restype = ctypes.c_int
        processor_count = ctypes.c_uint32()
        info = ctypes.POINTER(ctypes.c_int)()
        info_count = ctypes.c_uint32()
        if libsystem.host_processor_info(libsystem.mach_host_self(), 2, ctypes.byref(processor_count), ctypes.byref(info), ctypes.byref(info_count)) != 0:
            return None
        try:
            values = [int(info[index]) for index in range(int(info_count.value))]
            total = float(sum(values))
            # processor_cpu_load_info: user, system, idle, nice.
            idle = float(sum(values[index] for index in range(2, len(values), 4)))
            return (total, total - idle) if total > 0 else None
        finally:
            address = ctypes.cast(info, ctypes.c_void_p).value
            if address:
                libsystem.vm_deallocate(libsystem.mach_task_self(), address, ctypes.sizeof(ctypes.c_int) * int(info_count.value))
    except (AttributeError, OSError):
        return None


def system_cpu_percent_from_times(previous: tuple[float, float] | None, current: tuple[float, float] | None) -> float:
    if previous is None or current is None:
        return 0.0
    total_delta = current[0] - previous[0]
    busy_delta = current[1] - previous[1]
    if total_delta <= 0 or busy_delta < 0:
        return 0.0
    return clamp_cpu_percent((busy_delta / total_delta) * 100.0)


def current_system_cpu_percent_from_ps() -> float | None:
    global _stats_host_fallback_warning_emitted
    if not _stats_host_fallback_warning_emitted:
        logger.warning("Stats host CPU fallback uses ps subprocess; native host counters were unavailable")
        _stats_host_fallback_warning_emitted = True
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


def current_system_memory_bytes() -> tuple[int, int] | None:
    """Return (total, used) host memory without requiring an optional dependency."""
    try:
        fields = {
            key.rstrip(":"): int(value) * 1024
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
            if (parts := line.split()) and len(parts) >= 2
            for key, value in [(parts[0], parts[1])]
        }
        total = fields.get("MemTotal", 0)
        available = fields.get("MemAvailable", fields.get("MemFree", 0))
        if total > 0 and 0 <= available <= total:
            return total, total - available
    except (OSError, ValueError):
        pass
    return current_darwin_system_memory_bytes()


class DarwinVmStatistics64(ctypes.Structure):
    """Prefix of macOS vm_statistics64_t needed for available-memory accounting."""

    _fields_ = [
        ("free_count", ctypes.c_uint32),
        ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32),
        ("_lifetime_counters", ctypes.c_uint64 * 9),
        ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32),
        ("_revision1_lifetime_counters", ctypes.c_uint64 * 4),
        ("compressor_page_count", ctypes.c_uint32),
        ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32),
        ("internal_page_count", ctypes.c_uint32),
        ("_revision2_and_3_counters", ctypes.c_uint64 * 13),
    ]


def current_darwin_system_memory_bytes() -> tuple[int, int] | None:
    """Read macOS VM counters through Mach APIs, with no fork/exec on the sampler path."""
    if sys.platform != "darwin":
        return None
    total = darwin_sysctl_value("hw.memsize", ctypes.c_uint64)
    if total is None or total <= 0:
        return None
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libsystem.mach_host_self.restype = ctypes.c_uint32
        libsystem.host_page_size.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        libsystem.host_page_size.restype = ctypes.c_int
        libsystem.host_statistics64.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint32)]
        libsystem.host_statistics64.restype = ctypes.c_int
        host = libsystem.mach_host_self()
        page_size = ctypes.c_uint32()
        if libsystem.host_page_size(host, ctypes.byref(page_size)) != 0 or page_size.value <= 0:
            return None
        counters = DarwinVmStatistics64()
        count = ctypes.c_uint32(ctypes.sizeof(counters) // ctypes.sizeof(ctypes.c_int))
        if libsystem.host_statistics64(host, 4, ctypes.cast(ctypes.byref(counters), ctypes.POINTER(ctypes.c_int)), ctypes.byref(count)) != 0:
            return None
        # Darwin's speculative pages are already included in free_count.
        available = int(counters.free_count) * int(page_size.value)
        return total, max(0, total - min(total, available))
    except (AttributeError, OSError):
        return None


def stats_GPU_gpu_metrics() -> dict[str, Any]:
    """Collect aggregate GPU device facts through the installed driver CLI, if present."""
    try:
        devices_result = subprocess.run(
            ["gpu-info", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=STATS_HOST_RESOURCE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if devices_result.returncode != 0:
        return {}
    devices: dict[str, dict[str, float | str]] = {}
    for line in devices_result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            index = int(parts[0])
            util = clamp_cpu_percent(float(parts[2]))
            memory_used = max(0.0, float(parts[3]))
            memory_total = max(0.0, float(parts[4]))
        except ValueError:
            continue
        key = f"gpu:{index}"
        devices[key] = {
            "label": f"GPU {index} ({parts[1]})" if parts[1] else f"GPU {index}",
            "util_percent": util,
            "memory_used_bytes": int(memory_used * 1024 * 1024),
            "memory_capacity_bytes": int(memory_total * 1024 * 1024),
        }
    if not devices:
        return {}
    return {"devices": devices}


def stats_macos_gpu_metrics(gpu_name: str = "") -> dict[str, Any]:
    """Use macOS IORegistry's public aggregate GPU counters; macOS exposes no per-process GPU API."""
    if sys.platform != "darwin":
        return {}
    try:
        result = subprocess.run(
            ["ioreg", "-a", "-r", "-d1", "-w0", "-c", "IOAccelerator"],
            capture_output=True,
            timeout=STATS_HOST_RESOURCE_TIMEOUT_SECONDS,
            check=False,
        )
        records = plistlib.loads(result.stdout) if result.returncode == 0 and result.stdout else []
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
        return {}
    if not isinstance(records, list):
        return {}
    memory = current_system_memory_bytes()
    total_memory = memory[0] if memory is not None else 0
    devices: dict[str, dict[str, float | str]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        stats = record.get("PerformanceStatistics")
        if not isinstance(stats, dict):
            continue
        # Current Apple-silicon drivers expose Device Utilization %; older
        # releases used GPU Activity(%). Missing telemetry is not a zero sample.
        util = stats.get(
            "Device Utilization %",
            stats.get("GPU Activity(%)", stats.get("GPU Activity")),
        )
        used = stats.get("In use system memory", stats.get("In use video memory", 0))
        if util is None:
            continue
        try:
            util_percent = clamp_cpu_percent(float(util))
            memory_used_bytes = max(0, int(used))
        except (TypeError, ValueError):
            continue
        devices[f"gpu:{index}"] = {
            "label": f"GPU {index} ({gpu_name})" if gpu_name else f"GPU {index}",
            "util_percent": util_percent,
            "memory_used_bytes": memory_used_bytes,
            "memory_capacity_bytes": total_memory,
        }
    return {"devices": devices} if devices else {}


_stats_hardware_metadata_lock = threading.RLock()
_stats_hardware_metadata_cache: dict[str, str] = {}
_stats_hardware_metadata_initialized = False


def stats_macos_hardware_metadata() -> dict[str, str]:
    """Return static Apple-silicon labels without treating unified memory as discrete VRAM."""
    try:
        result = subprocess.run(
            ["system_profiler", "-json", "SPHardwareDataType", "SPMemoryDataType", "SPDisplaysDataType"],
            capture_output=True,
            text=True,
            timeout=STATS_HOST_RESOURCE_TIMEOUT_SECONDS,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 and result.stdout else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    hardware = next((item for item in payload.get("SPHardwareDataType", []) if isinstance(item, dict)), {})
    memory = next((item for item in payload.get("SPMemoryDataType", []) if isinstance(item, dict)), {})
    display = next((item for item in payload.get("SPDisplaysDataType", []) if isinstance(item, dict)), {})
    chip = str(hardware.get("chip_type") or display.get("sppci_model") or "").strip()
    core_fields = re.findall(r"\d+", str(hardware.get("number_processors") or ""))
    cpu_detail = chip
    if len(core_fields) >= 3:
        cpu_detail = f"{chip} · {core_fields[0]} cores ({core_fields[1]} performance + {core_fields[2]} efficiency)" if chip else f"{core_fields[0]} cores ({core_fields[1]} performance + {core_fields[2]} efficiency)"
    elif core_fields:
        cpu_detail = f"{chip} · {core_fields[0]} cores" if chip else f"{core_fields[0]} cores"
    memory_type = str(memory.get("dimm_type") or "").strip()
    metadata = {"cpu_label": cpu_detail, "gpu_label": str(display.get("sppci_model") or chip).strip()}
    if memory_type:
        metadata["system_memory_label"] = f"{memory_type} unified memory"
    return {key: value for key, value in metadata.items() if value}


def stats_host_hardware_metadata() -> dict[str, str]:
    global _stats_hardware_metadata_initialized
    with _stats_hardware_metadata_lock:
        if _stats_hardware_metadata_initialized:
            return dict(_stats_hardware_metadata_cache)
        metadata = stats_macos_hardware_metadata() if sys.platform == "darwin" else {}
        _stats_hardware_metadata_cache.clear()
        _stats_hardware_metadata_cache.update(metadata)
        _stats_hardware_metadata_initialized = True
        return dict(_stats_hardware_metadata_cache)


def stats_gpu_metrics() -> dict[str, Any]:
    hardware = stats_host_hardware_metadata()
    gpu = stats_GPU_gpu_metrics() if sys.platform != "darwin" else stats_macos_gpu_metrics(hardware.get("gpu_label", ""))
    return {
        "gpu_devices": gpu.get("devices", {}) if isinstance(gpu, dict) else {},
        "gpu_util_processes": {},
        "gpu_memory_processes": {},
    }


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
SERVER_CPU_BUDGET_PERCENT = 30.0
SERVER_CPU_BUDGET_SUSTAINED_SECONDS = 300.0
BACKGROUND_REFRESH_EVENT_LOG_SAMPLE_EVERY = 25
BACKGROUND_CLIENT_EVENTS_PATH = common.STATE_DIR / "background-owner" / "client-events.json"
# The event's storage owner determines whether another server must be notified immediately.
# Keep this table next to the transport rather than letting each write path choose between a
# local publish and a poll-dependent refresh.
BACKGROUND_CLIENT_EVENT_POLICIES: dict[str, dict[str, str]] = {
    "attention_acks_changed": {"truth": "tmux-ai-status", "delivery": "push"},
    "auto_approve_changed": {"truth": "tmux workers and yolomux state", "delivery": "push"},
    "background_owner_changed": {"truth": "background-owner", "delivery": "push"},
    "background_refresh_done": {"truth": "background owner", "delivery": "push"},
    "chat_messages_changed": {"truth": "chat database", "delivery": "push"},
    "chat_typing_changed": {"truth": "chat database", "delivery": "push"},
    "settings_changed": {"truth": "settings file", "delivery": "push"},
    "pricing_catalog_changed": {"truth": "pricing catalog", "delivery": "push"},
    "yoagent_conversation_changed": {"truth": "yoagent conversation", "delivery": "push"},
}
BACKGROUND_CLIENT_EVENT_TYPES = frozenset(
    event_type
    for event_type, policy in BACKGROUND_CLIENT_EVENT_POLICIES.items()
    if policy["delivery"] == "push"
)
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
    "status_column",
    "status_row_from_bottom",
    "status_spinner_advanced",
    "status_tokens",
    "metadata_badge_pulse_remaining_ms",
    "timings",
    "title",
    "working_elapsed_seconds",
})
DIRECTORY_WATCH_ENTRY_LIMIT = 512
NATIVE_FILESYSTEM_WATCH_DEBOUNCE_MS = 250
NATIVE_FILESYSTEM_WATCH_STEP_MS = 50
NATIVE_FILESYSTEM_WATCH_RUST_TIMEOUT_MS = 1_000
NATIVE_FILESYSTEM_RECONCILE_SECONDS = 300.0
NATIVE_FILESYSTEM_RETRY_SECONDS = 10.0
# Native watching is preferred; when unavailable, only visible Finder/Differ
# roots are polled at this bounded cadence.
VISIBLE_FILESYSTEM_FALLBACK_POLL_SECONDS = 2.0
# Keep in sync with tmuxSessionNameError() in static/yolomux.js.
TMUX_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,64}$")
DEFAULT_APP_SETTINGS = default_settings()
DEFAULT_PERFORMANCE_SETTINGS = DEFAULT_APP_SETTINGS["performance"]
SELF_RESTART_LOG_PATH = "/tmp/yolomux-self-update-restart.log"
SELF_RESTART_ENV_KEYS = (
    "PATH",
    "TERM",
    "PYTHONUNBUFFERED",
    "MALLOC_ARENA_MAX",
    "YOLOMUX_EXTRA_PATH",
    "YOLOMUX_CONFIG_DIR",
    "YOLOMUX_STATE_DIR",
    "YOLOMUX_TEST_AUTH_BYPASS",
    "VIRTUAL_ENV",
)
XTERM_RUNTIME_ASSETS = {
    "xterm.js": {
        "node_path": Path("node_modules/@xterm/xterm/lib/xterm.js"),
        "url": "https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/lib/xterm.js",
    },
    "xterm.css": {
        "node_path": Path("node_modules/@xterm/xterm/css/xterm.css"),
        "url": "https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/css/xterm.css",
    },
    "xterm-addon-unicode11.js": {
        "node_path": Path("node_modules/@xterm/addon-unicode11/lib/addon-unicode11.js"),
        "url": "https://cdn.jsdelivr.net/npm/@xterm/addon-unicode11@0.9.0/lib/addon-unicode11.js",
    },
}


def xterm_runtime_assets_ready(root: str | Path) -> bool:
    root_path = Path(root)
    return all(
        (root_path / "static" / name).is_file() or (root_path / details["node_path"]).is_file()
        for name, details in XTERM_RUNTIME_ASSETS.items()
    )


def ensure_xterm_runtime_assets(root: str | Path) -> tuple[bool, str]:
    """Install the declared xterm packages when an update leaves any runtime asset absent."""
    root_path = Path(root)
    if xterm_runtime_assets_ready(root_path):
        return True, ""
    npm = shutil.which("npm")
    if npm:
        try:
            result = subprocess.run(
                [npm, "install", "--no-audit", "--no-fund", "--silent"],
                cwd=root_path,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0 and xterm_runtime_assets_ready(root_path):
            return True, ""
    curl = shutil.which("curl")
    if not curl:
        return False, "curl is required to download xterm runtime assets"
    static_dir = root_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    for name, details in XTERM_RUNTIME_ASSETS.items():
        destination = static_dir / name
        if destination.is_file() or (root_path / details["node_path"]).is_file():
            continue
        temporary = destination.with_name(f".{name}.{os.getpid()}.tmp")
        try:
            result = subprocess.run(
                [curl, "--fail", "--location", "--silent", "--show-error", "--connect-timeout", "10", "--retry", "2", "--output", str(temporary), details["url"]],
                cwd=root_path,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                detail = (result.stderr or result.stdout or "download failed").strip()
                temporary.unlink(missing_ok=True)
                return False, f"xterm asset download failed: {detail[:400]}"
            temporary.replace(destination)
        except (OSError, subprocess.SubprocessError) as exc:
            temporary.unlink(missing_ok=True)
            return False, f"xterm asset download failed: {exc}"
    return (True, "") if xterm_runtime_assets_ready(root_path) else (False, "xterm assets are still missing")


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


@dataclass
class InputHeartbeatRecord:
    condition: threading.Condition = field(default_factory=threading.Condition)
    pending: dict[tuple[str, str], PendingInputHeartbeat] = field(default_factory=dict)
    flush_active: bool = False
    stop_requested: bool = False
    worker: threading.Thread | None = None


@dataclass
class BackgroundRefreshEventLogRecord:
    count: int = 0
    last_emit_count: int = 0


def session_files_batch_worker_count(count: int, maximum: int = SESSION_FILES_BATCH_MAX_WORKERS) -> int:
    return max(1, min(max(1, int(maximum)), count))


def tmux_command_failure_payload(session: str, diagnostic: str, **fields: Any) -> dict[str, Any]:
    return {
        "session": session,
        **fields,
        "diagnostic": diagnostic,
        **user_message_payload(
            "terminal.window.failed",
            diagnostic,
            error=message_descriptor("common.requestFailed", "request failed"),
        ),
    }


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
        # Root interest is written per server, while only the elected background owner is
        # allowed to sample the directories.  Keep that sample in a separate atomic record so
        # followers can compare the owner's delta without lstat/iterdir work of their own.
        self.signature_path = self.path.with_name(f"{self.path.name}.signatures.json")
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

    @staticmethod
    def _freeze_signature(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(SharedWatchRootIndex._freeze_signature(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((str(key), SharedWatchRootIndex._freeze_signature(item)) for key, item in value.items()))
        return value

    def publish_signature_snapshot(self, signature: tuple[Any, ...]) -> bool:
        """Atomically publish the one owner-scanned signature when it actually changes."""

        frozen = self._freeze_signature(signature)
        if not isinstance(frozen, tuple):
            frozen = ()
        with file_lock(self.signature_path):
            previous = self.signature_snapshot()
            if previous == frozen:
                return False
            payload = {"version": 1, "signature": frozen, "updated_at": self._clock()}
            atomic_write_text(self.signature_path, json.dumps(payload, separators=(",", ":"), sort_keys=True), mode=0o600)
        return True

    def signature_snapshot(self) -> tuple[Any, ...]:
        try:
            raw = json.loads(self.signature_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ()
        if not isinstance(raw, dict):
            return ()
        frozen = self._freeze_signature(raw.get("signature"))
        return frozen if isinstance(frozen, tuple) else ()


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


def filesystem_changed_paths(previous: tuple[Any, ...] | None, current: tuple[Any, ...] | None) -> list[str]:
    """Return the smallest directly observed subtrees for index invalidation."""
    previous_by_root = filesystem_signature_root_map(previous)
    current_by_root = filesystem_signature_root_map(current)
    changed_paths: set[str] = set()
    for root in sorted(set(previous_by_root) | set(current_by_root)):
        previous_signature = previous_by_root.get(root)
        current_signature = current_by_root.get(root)
        if previous_signature == current_signature:
            continue
        if previous_signature is None or current_signature is None:
            changed_paths.add(root)
            continue
        previous_entries = filesystem_signature_entry_map(previous_signature)
        current_entries = filesystem_signature_entry_map(current_signature)
        names = set(previous_entries) | set(current_entries)
        direct_changes = sorted(name for name in names if previous_entries.get(name) != current_entries.get(name))
        if not direct_changes:
            changed_paths.add(root)
            continue
        changed_paths.update(str(Path(root) / name) for name in direct_changes)
    return sorted(changed_paths)


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


def tmux_session_name_sanitize(name: str) -> str:
    # tmux's own session_check_name() silently rewrites "." and ":" to "_": a rename to
    # "dynamo-utils.dev" is stored by tmux as "dynamo-utils_dev". Mirror that here so the name we
    # validate, collision-check, return, and switch to matches what tmux actually stored -- otherwise
    # the rename returns rc=0 but the follow-up switch targets a session name that never existed.
    return re.sub(r"[.:]", "_", str(name or "").strip())


def tmux_session_name_error(name: str) -> str | None:
    if not name:
        return "session name is required"
    if len(name) > 64:
        return "session name must be 64 characters or fewer"
    if not TMUX_SESSION_NAME_RE.fullmatch(name):
        return "session name may contain only letters, numbers, spaces, dot, dash, and underscore"
    return None


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


class YoagentAppField:
    def __init__(self, name: str):
        self.name = name

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self
        return getattr(instance._app, self.name)

    def __set__(self, instance: Any, value: Any) -> None:
        setattr(instance._app, self.name, value)


class YoagentGlobal:
    def __init__(self, name: str):
        self.name = name

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self
        return globals()[self.name]

    def __set__(self, instance: Any, value: Any) -> None:
        globals()[self.name] = value


class YoagentAppDeps:
    app_fields = """activity_summary_payload auto_approve_prompt_source client_event_payload_signature float_value log_event
    publish_client_event publish_yoagent_conversation_changed publish_yoagent_stream_delta record_yoagent_message require_known_session
    run_yoagent_direct_prompt_backend save_settings sessions settings_payload tmux_recency_ordered_sessions wake_client_event_watcher
    yoagent_action_lock yoagent_action_previews yoagent_action_waits yoagent_chat_request_lock yoagent_chat_requests yoagent_cli_lock
    transcript_compact_view yoagent_cli_sessions yoagent_codex_app_server yoagent_codex_app_server_key yoagent_codex_app_server_lock yoagent_conversation_payload
    yoagent_job_lock yoagent_jobs yoagent_managed_targets yoagent_prewarm_lock yoagent_prewarm_record yoagent_session_summaries
    yoagent_session_summary_lock yoagent_settings yoagent_skill_file_answer yoagent_skills_payload yoagent_stream_auxiliary_message_fields
    yoagent_stream_callback yoagent_summary_worker_lock yoagent_summary_worker_record yoagent_transports""".split()
    global_callables = """agent_screen_state codex_event_session_id discover_sessions hybrid_approval_prompt_state normalized_prompt_state
    mutate_yolomux_state read_yolomux_state resolve_yoagent_backend tmux_capture_pane tmux_capture_pane_styled tmux_clear_input tmux_paste_text
    transcript_activity_is_recent update_yolomux_state yoagent_activity_payload_signature yoagent_cli_auth_failure
    strip_yoagent_hidden_thinking strip_yoagent_stream_hidden_thinking yoagent_cli_fallback_reason yoagent_language_directive""".split()

    def __init__(self, app: Any):
        # The controller gets only these explicit app/global capabilities. Its own operations are
        # called on the controller directly, so dependency lookup cannot silently cross ownership.
        self._app = app


for _yoagent_app_field in YoagentAppDeps.app_fields:
    setattr(YoagentAppDeps, _yoagent_app_field, YoagentAppField(_yoagent_app_field))
for _yoagent_global_callable in YoagentAppDeps.global_callables:
    setattr(YoagentAppDeps, _yoagent_global_callable, YoagentGlobal(_yoagent_global_callable))
del _yoagent_app_field, _yoagent_global_callable


class TmuxWebtermApp:
    def __init__(self, sessions: list[str], dangerously_yolo: bool = False, *, status_service_mode: bool = False):
        self.sessions = sessions
        self.dangerously_yolo = dangerously_yolo
        self.status_service_mode = status_service_mode
        self.share_tokens: dict[str, dict[str, Any]] = {}
        self.share_tokens_lock = threading.RLock()
        self.metadata_cache = MetadataCache()
        self.chat_store = ChatStore(common.STATE_DIR / "yochat.sqlite3")
        self.chat_service = ChatService(
            self.chat_store,
            cursor_secret_path=common.STATE_DIR / "chat-cursor.key",
            retention_days=self.chat_retention_days,
        )
        # Shared login throttle: one WAL SQLite file under the state dir enforces the
        # policy across every port pointing here. Admission runs before PBKDF2 in the
        # auth mixin; policy overrides are validated at load and fall back to defaults.
        self.login_rate_limiter = LoginRateLimiter(
            common.STATE_DIR / LOGIN_THROTTLE_DATABASE_NAME,
            policy=load_login_rate_policy(common.CONFIG_DIR / LOGIN_THROTTLE_OVERRIDE_NAME),
        )
        # Optional, OFF-BY-DEFAULT attack-response escalation (defense in depth, not the
        # core 429). The edge controller only ever spawns a firewall process when an
        # operator enables it; disabled, block() is a no-op. See login_escalation.py.
        self.login_edge_controller = EdgeBlockController(runner=default_edge_runner, enabled=False)
        # DOIT.58 Phase 1: per-session/window user+agent activity ledger (heartbeat-coalesced
        # typed-time). Constructor defaults today; Preferences exposure is a deferred follow-up.
        self.activity_ledger = ActivityLedger(ACTIVITY_PATH, heartbeat_path=ACTIVITY_HEARTBEATS_PATH)
        self.activity_ledger.load()
        self.activity_heartbeat_next_rotate_at = 0.0
        self.input_heartbeat_record = InputHeartbeatRecord()
        self.session_files_service = SessionFilesService()
        self.activity_transcript_service = ActivityTranscriptService()
        self.client_watch_service = ClientWatchService()
        self.auto_approve_cache_condition = threading.Condition(threading.RLock())
        self.auto_approve_cache_record = AutoApproveCacheRecord()
        self.tmux_signal_cache = TtlCache(TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS, max_entries=1)
        self.tmux_signal_event_watcher: TmuxSignalEventWatcher | None = None
        self.client_watch_service.tmux_signal_payload: dict[str, Any] | None = None
        self.tmux_snapshot_history_lock = threading.RLock()
        self.tmux_snapshot_history_signatures: dict[tuple[str, str, int], tuple[int, int]] = {}
        # last-logged watched-PR truncation state, so the cap is logged only when it changes.
        self._watched_pr_truncated_signature: tuple[int, tuple[str, ...]] | None = None
        self.metadata_warm_lock = threading.Lock()
        self.metadata_warm_record = MetadataWarmRecord()
        self.metadata_badge_lock = threading.Lock()
        self.metadata_badge_records: dict[str, MetadataBadgeRecord] = {}
        self.stats_collection_state = StatsCollectionState()
        self.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()
        self.job_client = JobClient()
        self.upload_retention_sweeper = UploadRetentionSweeper()
        self.approval_client = ApprovalClient()
        self.status_client = StatusClient()
        self.attention_ack_lock = threading.RLock()
        self.attention_ack_keys: dict[str, float] = {}
        self.agent_window_transition_lock = threading.RLock()
        self.agent_window_transition_state: dict[str, dict[str, float | str]] = {}
        self.performance_record_lock = threading.RLock()
        self.performance_records: collections.deque[dict[str, Any]] = collections.deque(maxlen=PERFORMANCE_RECORD_LIMIT)
        self.background_refresh_event_log_lock = threading.Lock()
        self.background_refresh_event_log_records: dict[tuple[str, str], BackgroundRefreshEventLogRecord] = {}
        self.client_events = ClientEventBroker()
        # Catalog startup is offline-only; the coordinator performs provider
        # fetches exclusively in its explicit background Refresh worker.
        self.pricing_catalog = PricingCatalog()
        self.pricing_refresh_coordinator = PricingRefreshCoordinator(
            self.pricing_catalog,
            publish=lambda event_type, payload: self.publish_background_client_event(
                event_type,
                payload,
                trigger="pricing-refresh",
                cache="ready",
            ),
        )
        self.watch_root_owner_id = f"{SERVER_HOSTNAME}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self.watch_root_index = SharedWatchRootIndex(WATCH_INDEX_PATH, owner_id=self.watch_root_owner_id)
        self.tmux_theme_color = ""
        self.yoagent_cli_lock = threading.RLock()
        self.yoagent_cli_sessions: dict[str, dict[str, Any]] = yoagent_conversation.load_cli_sessions(monotonic_now=time.monotonic())
        self.yoagent_transports = default_yoagent_transport_registry()
        self.yoagent_controller = YoagentController(YoagentAppDeps(self))
        self.yoagent_managed_targets: dict[str, dict[str, Any]] = {}
        self.yoagent_streams = YoagentStreamPublisher(
            publish_client_event=lambda *args, **kwargs: self.publish_client_event(*args, **kwargs),
            publish_stream_delta=self.publish_yoagent_stream_delta,
        )
        self.yoagent_stream_lock = self.yoagent_streams.store.lock
        self.yoagent_stream_states = self.yoagent_streams.store.states
        self.yoagent_chat_request_lock = threading.RLock()
        self.yoagent_chat_requests: dict[str, dict[str, Any]] = {}
        self.yoagent_action_lock = threading.RLock()
        self.yoagent_action_previews: dict[str, dict[str, Any]] = {}
        self.yoagent_action_waits: dict[str, dict[str, Any]] = {}
        self.yoagent_job_lock = threading.RLock()
        self.yoagent_jobs: dict[str, dict[str, Any]] = self.yoagent_controller.load_yoagent_jobs()
        self.yoagent_prewarm_lock = threading.Lock()
        self.yoagent_prewarm_record = YoagentPrewarmRecord()
        self.yoagent_codex_app_server_lock = threading.RLock()
        self.yoagent_codex_app_server: CodexAppServerSession | None = None
        self.yoagent_codex_app_server_key = ""
        self.yoagent_session_summary_lock = threading.RLock()
        self.yoagent_session_summaries: dict[str, dict[str, Any]] = {}
        self.yoagent_summary_worker_lock = threading.Lock()
        self.yoagent_summary_worker_record = YoagentSummaryWorkerRecord()
        self.update_check_thread: threading.Thread | None = None
        self._update_last_target: str | None = None
        self.load_metadata_badge_state()
        self.yoagent_controller.load_yoagent_session_summaries()
        self.event_log = EventLog(EVENT_LOG_PATH)
        self.run_history_store = RunHistoryStore(RUN_HISTORY_PATH)
        self.control_server = YolomuxControlServer(self.handle_control_request)
        if not status_service_mode:
            self.control_server.start()
        self.background_owner: BackgroundOwnerRegistry | DisabledBackgroundOwner = DisabledBackgroundOwner()
        self.search_indexer = SearchIndexerClient()
        self.stats_current_client = StatsCurrentClient()
        self.stats_current_http = StatsHttpForwarder(
            self.stats_current_client,
            client_binding_secret=common.AUTH_COOKIE_SECRET,
        )
        self.stats_current_runtime = StatsCurrentRuntime(
            self.stats_current_client,
            {
                "cpu": self.collect_current_stats_cpu,
                "agent_status": self.collect_current_stats_agent_status,
                "gpu": self.collect_current_stats_gpu,
                "service_load": self.collect_current_stats_service_load,
                "system_memory": self.collect_current_stats_system_memory,
                "agent_tokens": self.collect_current_stats_agent_tokens,
            },
            owner_generation=self.stats_current_owner_generation,
            token_cadence_seconds=self.stats_current_token_cadence_seconds,
        )
        # A persistent child owns all Quick Open builds and SQLite writes.
        # HTTP/WebSocket processes remain read-only index consumers.
        file_index.set_background_owner_checker(self.search_index_can_build)
        file_index.set_background_owner_refresh_requester(self.request_background_refresh)
        file_index.set_background_index_search_requester(self.request_background_index_search)
        file_index.set_background_owner_bytes_recorder(self.record_background_search_index_bytes_written)
        file_index.set_background_owner_done_notifier(self.publish_background_refresh_done)

    def require_known_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus] | None:
        # The standard "unknown session -> 404" guard. Decorated handlers use requires_known_session();
        # payload-driven helpers and non-HTTP response shapes keep explicit checks.
        if session not in self.sessions:
            diagnostic = f"unknown session: {session}"
            return user_message_payload("status.sessionEnded", diagnostic, session=session), HTTPStatus.NOT_FOUND
        return None

    def stats_current_process_identity(self) -> tuple[str, str, int]:
        owner = self.background_owner.owner_payload()
        try:
            port = max(0, int(owner.get("port") or 0))
        except (TypeError, ValueError):
            port = 0
        pid = os.getpid()
        key = f"port:{port}" if port else f"pid:{pid}"
        label = f"yolomux.py :{port}" if port else f"yolomux.py PID {pid}"
        return key, label, port

    def tmux_ai_status_empty(self) -> dict[str, Any]:
        return {
            "version": TMUX_AI_STATUS_VERSION,
            "rev": 0,
            "updated_at": 0.0,
            "attention_acks": {"rev": 0, "updated_at": 0.0, "keys": {}},
            "attention_instances": {"updated_at": 0.0, "instances": {}},
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
        attention_instances = data.get("attention_instances") if isinstance(data.get("attention_instances"), dict) else {}
        status["attention_instances"] = attention_instances
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

    def stats_agent_window_rows(self) -> list[dict[str, Any]]:
        payload = self.status_snapshot_payload()
        return self.stats_agent_window_rows_from_auto_approve_payload(payload) if payload is not None else []

    def status_snapshot_payload(self) -> AutoApproveStatusPayload | None:
        """Decode statusd's completed public snapshot for non-HTTP consumers only."""

        if not self.sessions:
            return None
        response, body = self.status_client.snapshot(self.sessions, timeout=1.0)
        if response.get("ok") is not True or not body:
            return None
        try:
            validate_status_snapshot(response, body)
            payload = json.loads(body.decode("utf-8"))
        except (StatusProtocolError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def fresh_auto_approve_payload_for_stats(self) -> AutoApproveStatusPayload | None:
        with self.auto_approve_cache_condition:
            cached = self.auto_approve_cache_record.payload
            if cached is None:
                return None
            stored_at, (payload, status) = cached
            if status != HTTPStatus.OK:
                return None
            age_seconds = time.monotonic() - stored_at
            if not isinstance(payload, dict):
                return None
        if age_seconds > AUTO_APPROVE_CACHE_MAX_AGE_SECONDS:
            self.start_auto_approve_cache_refresh()
        return copy.deepcopy(payload)

    @staticmethod
    def agent_window_snapshot_rows_by_target(payload: AutoApproveStatusPayload) -> tuple[int, dict[tuple[str, str, str], dict[str, Any]]]:
        """Return the roster-owned state rows keyed by session, pane target, and client kind."""

        try:
            revision = max(0, int(payload.get("agent_window_snapshot_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        sessions_payload = payload.get("sessions")
        if not isinstance(sessions_payload, dict):
            return revision, rows
        for session, session_payload in sessions_payload.items():
            if not isinstance(session_payload, dict):
                continue
            window_rows = session_payload.get("agent_windows")
            if not isinstance(window_rows, list):
                continue
            for row in window_rows:
                if not isinstance(row, dict):
                    continue
                target = str(row.get("pane_target") or "")
                kind = str(row.get("kind") or "").lower()
                if target and kind:
                    rows[(str(session), target, kind)] = copy.deepcopy(row)
        return revision, rows

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

    def notification_transition_seconds(self) -> float:
        return self.performance_setting_seconds("workflow_transition_glow_seconds", 0.0, 300.0)

    def stats_agent_cooldown_visible(self, row: dict[str, Any], sample_time: float, transition_seconds: float) -> bool:
        if row.get("cooldown_acknowledged") is True:
            return False
        stopped_ts = self.float_value(row.get("working_stopped_ts"), 0.0)
        if stopped_ts <= 0:
            return False
        return True

    def stats_agent_activity_kind_locked(self, row: dict[str, Any], key: str, sample_time: float, transition_seconds: float) -> str:
        state = str(row.get("state") or "").strip().lower()
        previous = self.stats_collection_state.agent_activity_state.get(key) if key else None
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
            self.stats_collection_state.agent_activity_state[key] = {
                "state": state,
                "kind": kind,
                "time": sample_time,
                "transition_started": transition_started,
            }
        return kind

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

    def stats_agent_token_rows(self, rows: list[dict[str, Any]], *, include_missing: bool = False) -> list[dict[str, Any]]:
        token_rows: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for index, row in enumerate(rows):
            key = self.stats_agent_token_key(row, index)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            transcript = str(row.get("transcript") or "").strip()
            kind = str(row.get("kind") or "").strip().lower()
            if not transcript and not include_missing:
                continue
            token_rows.append({
                "key": key,
                "label": self.stats_agent_token_label(row),
                "transcript": transcript,
                "kind": kind,
                "session": str(row.get("session") or "").strip(),
                "window": str(row.get("window_index") if isinstance(row.get("window_index"), int) else row.get("window") or "").strip(),
                "window_label": str(row.get("window_label") or row.get("label") or row.get("window") or "").strip(),
            })
        return token_rows

    def stats_current_owner_generation(self) -> int | None:
        if not self.background_can_run(BACKGROUND_ROLE_STATS_SAMPLER):
            return None
        started_at_ns = self.background_owner.owner_payload().get("started_at_ns")
        if isinstance(started_at_ns, bool) or not isinstance(started_at_ns, int):
            return None
        return started_at_ns if started_at_ns >= 0 else None

    def stats_current_token_cadence_seconds(self) -> float:
        return STATS_AGENT_TOKEN_SAMPLE_SECONDS if self.client_events.has_demand("stats") else STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS

    def record_current_browser_observations(
        self,
        payload: object,
        *,
        authenticated_username: str,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        """Validate and append one bounded batch of original browser facts."""

        try:
            observations = stats_current_observations.parse_browser_observations(
                payload,
                client_binding_secret=common.AUTH_COOKIE_SECRET,
                authenticated_username=authenticated_username,
            )
        except stats_current_observations.BrowserObservationUpgradeRequired:
            return stats_current_protocol.upgrade_required_response(
                stats_current_storage.MIN_WRITER_PROTOCOL,
                stats_current_storage.SCHEMA_VERSION,
                stats_current_revision.CURRENT_CODE_REVISION,
            ), HTTPStatus.UPGRADE_REQUIRED
        except stats_current_observations.BrowserObservationError as error:
            return {
                "ok": False,
                "status": "unsupported",
                "reason": str(error)[:256],
            }, HTTPStatus.BAD_REQUEST
        if not self.stats_current_client.ensure_started():
            status = self.stats_current_client.status()
            if (
                status.get("status") == "upgrade_required"
                or status.get("error_code") == "upgrade_required"
            ):
                return status, HTTPStatus.UPGRADE_REQUIRED
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "statsd unavailable",
            }, HTTPStatus.SERVICE_UNAVAILABLE
        response = self.stats_current_client.append(observations=observations)
        if response.get("ok") is not True:
            if (
                response.get("status") == "upgrade_required"
                or response.get("error_code") == "upgrade_required"
            ):
                return response, HTTPStatus.UPGRADE_REQUIRED
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "statsd unavailable",
            }, HTTPStatus.SERVICE_UNAVAILABLE
        return {
            "ok": True,
            "source_generation": response.get("source_generation", 0),
            "accepted": response.get("accepted", 0),
            "duplicates": response.get("duplicates", 0),
        }, HTTPStatus.OK

    def collect_current_stats_cpu(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        sample, _recorded = self.current_stats_sample(force=True)
        self.update_server_cpu_budget(sample)
        process_id, _label, _port = self.stats_current_process_identity()
        return stats_current_collectors.cpu_success(
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            source_id=process_id,
            process_percent=float(sample["cpu_percent"]),
            system_percent=float(sample["system_cpu_percent"]),
        )

    def collect_current_stats_agent_status(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        rows = self.stats_agent_window_rows()
        states: dict[str, str] = {}
        seen_keys: set[str] = set()
        transition_seconds = self.notification_transition_seconds()
        with self.stats_collection_state.agent_activity_lock:
            for index, row in enumerate(rows):
                key = self.stats_agent_token_key(row, index)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                states[key] = self.stats_agent_activity_kind_locked(
                    row,
                    key,
                    attempt.scheduled_at,
                    transition_seconds,
                )
            for key in list(self.stats_collection_state.agent_activity_state):
                if key not in seen_keys:
                    self.stats_collection_state.agent_activity_state.pop(key, None)
        process_id, _label, _port = self.stats_current_process_identity()
        return stats_current_collectors.agent_status_success(
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            source_id=process_id,
            states=states,
        )

    def collect_current_stats_gpu(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        devices = stats_gpu_metrics().get("gpu_devices")
        if not isinstance(devices, dict):
            return stats_current_collectors.CollectorFacts()
        samples = []
        for source_id, row in devices.items():
            if not isinstance(row, dict):
                continue
            util = row.get("util_percent")
            used = row.get("memory_used_bytes")
            capacity = row.get("memory_capacity_bytes")
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (util, used, capacity)):
                continue
            samples.append(stats_current_collectors.GpuDeviceSample(
                str(source_id),
                float(util),
                float(used),
                float(capacity),
                str(row.get("label") or source_id),
            ))
        return stats_current_collectors.gpu_devices_success(
            samples,
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
        )

    def collect_current_stats_service_load(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        # The CPU family is the single owner of the yolomux.py web-process metric. Keeping
        # it out of Services avoids rendering one PID twice at different collector cadences.
        rows = [
            row for row in self.runtime_local_services().get("services") or []
            if isinstance(row, dict) and str(row.get("service") or "").strip() != "web"
        ]
        samples = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("service") or "").strip()
            if not source_id:
                continue
            resources = row.get("resources") if isinstance(row.get("resources"), dict) else {}
            cpu = resources.get("cpu_percent")
            rss = resources.get("rss_bytes")
            running = isinstance(row.get("pid"), int) and row["pid"] > 0
            samples.append(stats_current_collectors.ServiceLoadSample(
                source_id,
                running,
                max(0.0, float(cpu)) if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) else 0.0,
                max(0.0, float(rss)) if running and isinstance(rss, (int, float)) and not isinstance(rss, bool) else None,
            ))
        return stats_current_collectors.service_load_success(
            samples,
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
        )

    def collect_current_stats_system_memory(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        memory = current_system_memory_bytes()
        if memory is None:
            raise RuntimeError("system memory metrics unavailable")
        return stats_current_collectors.system_memory_success(
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            source_id="host",
            used_bytes=float(memory[1]),
            capacity_bytes=float(memory[0]),
        )

    def collect_current_stats_agent_tokens(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        rows = self.stats_agent_window_rows()
        if self.sessions and not rows:
            raise RuntimeError("agent roster unavailable")
        atoms = []
        tombstones = []
        scan = self.stats_current_transcript_usage.scan(self.stats_agent_token_rows(rows))
        current_settings = self.settings_payload().get("settings", {})
        for item in scan.items:
            fields = dict(vars(item.atom))
            fields["tmux_key"] = item.tmux_key
            fields["agent_kind"] = item.agent_kind
            if fields.get("pricing_profile", "default") == "default":
                fields["pricing_profile"] = configured_usage_pricing_profile(
                    current_settings,
                    provider=str(fields.get("provider") or ""),
                    execution_source=item.agent_kind,
                    endpoint=str(fields.get("endpoint") or ""),
                    observed_at=item.atom.timestamp,
                )
            try:
                atoms.append(stats_current_usage.usage_atom_from_source(fields))
            except stats_current_usage.UsageValidationError:
                continue
        for item in scan.tombstones:
            tombstones.append(
                stats_current_usage.legacy_fork_usage_tombstone_from_source(
                    vars(item.atom)
                )
            )
        process_id, _label, _port = self.stats_current_process_identity()
        return stats_current_collectors.usage_scan_success(
            atoms,
            tombstones,
            stats_current_collectors.CollectorReceipt(
                lambda: self.stats_current_transcript_usage.commit(scan.receipt_id),
                lambda: self.stats_current_transcript_usage.rollback(scan.receipt_id),
            ),
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            source_id=process_id,
            budget_exhausted_follow_up=scan.budget_exhausted,
        )

    def latest_stats_sample(self) -> dict[str, Any]:
        """Read the last scheduler-owned CPU sample without collecting in an API thread."""

        with self.stats_collection_state.sample_lock:
            cached = self.stats_collection_state.sample_record.cached_payload
            if cached is not None:
                return dict(cached)
        now = time.time()
        return {
            "time": now, "pid": os.getpid(), "started_at": SERVER_STARTED_AT,
            "uptime_seconds": max(0.0, now - SERVER_STARTED_AT), "cpu_percent": 0.0,
            "system_cpu_percent": 0.0, "rss_bytes": 0,
        }

    def current_stats_sample(self, *, force: bool = False) -> tuple[dict[str, Any], bool]:
        now = time.time()
        monotonic_now = time.monotonic()
        with self.stats_collection_state.sample_lock:
            record = self.stats_collection_state.sample_record
            cached = record.cached_payload
            cached_monotonic = record.cached_monotonic
            use_cached = (
                not force
                and cached is not None
                and cached_monotonic is not None
                and monotonic_now - cached_monotonic < STATS_SAMPLE_CACHE_SECONDS
            )
            if use_cached:
                sample = dict(cached)
                record_cpu_sample = False
            else:
                process_time = time.process_time()
                cpu_percent = 0.0
                if record.last_monotonic is not None and record.last_process_time is not None:
                    elapsed = monotonic_now - record.last_monotonic
                    cpu_elapsed = process_time - record.last_process_time
                    if elapsed > 0 and cpu_elapsed >= 0:
                        cpu_percent = clamp_cpu_percent((cpu_elapsed / elapsed) * 100.0)
                system_cpu_times = current_system_cpu_times()
                if system_cpu_times is not None:
                    system_cpu_percent = system_cpu_percent_from_times(record.last_system_cpu_times, system_cpu_times)
                    record.last_system_cpu_times = system_cpu_times
                else:
                    system_cpu_percent = current_system_cpu_percent_from_ps() or 0.0
                record.last_monotonic = monotonic_now
                record.last_process_time = process_time
                sample = {
                    "time": now,
                    "pid": os.getpid(),
                    "started_at": SERVER_STARTED_AT,
                    "uptime_seconds": max(0.0, now - SERVER_STARTED_AT),
                    "cpu_percent": round(cpu_percent, 3),
                    "system_cpu_percent": round(system_cpu_percent, 3),
                    "rss_bytes": current_process_rss_bytes(),
                }
                record.cached_monotonic = monotonic_now
                record.cached_payload = dict(sample)
                record_cpu_sample = True
        return sample, record_cpu_sample

    def start_background_owner(self, port: int | None = None, priority: int = 0) -> bool:
        self.background_owner = BackgroundOwnerRegistry(
            control_socket=str(self.control_server.path),
            port=port,
            project_root=str(PROJECT_ROOT),
            on_demote=self.demote_background_owner,
            on_acquire=self.handle_background_owner_acquired,
            priority=priority,
            capabilities={
                "stats_writer_build": stats_current_storage.MIN_WRITER_BUILD,
            },
        )
        file_index.set_background_owner_checker(self.search_index_can_build)
        acquired = self.background_owner.start()
        if not acquired:
            acquired = self.background_owner.attempt_required_capability_takeover(
                "stats_writer_build",
                stats_current_storage.MIN_WRITER_BUILD,
            )
        if not acquired and self.background_owner.status == "blocked_by_unreachable_owner":
            self.log_event(
                None,
                "background_owner_blocked",
                "Background owner takeover blocked",
                self.background_owner.status_payload(),
                message_key="events.message.backgroundOwner.blocked",
            )
        return acquired

    def handle_background_owner_acquired(self, status: dict[str, Any]) -> None:
        transition = str(status.get("last_transition") or "acquired")
        if transition == "takeover":
            self.log_event(
                None,
                "background_owner_takeover",
                "Background owner moved to this server",
                status.get("last_transition_details", {}),
                message_key="events.message.backgroundOwner.takeover",
            )
        else:
            self.log_event(
                None,
                "background_owner_acquired",
                "Background owner acquired by this server",
                status.get("generation", {}),
                message_key="events.message.backgroundOwner.acquired",
            )
        # jobd is started only by the elected scheduler owner.  HTTP handlers
        # can submit/read work but must never create a child process themselves.
        self.job_client.start_for_scheduler()
        self.pricing_refresh_coordinator.start_periodic()
        self.stats_current_runtime.start()
        self.warm_start_session_files_payload_cache()
        self.warm_start_tabber_activity_cache()
        self.publish_background_client_event("background_owner_changed", self.background_owner.status_payload(), trigger="background-owner", cache="ready")

    def background_can_run(self, role: str) -> bool:
        return self.background_owner.can_run(role)

    def search_index_can_build(self, role: str) -> bool:
        """Only the persistent indexer child may mutate Quick Open indexes."""
        return False if role == BACKGROUND_ROLE_SEARCH_INDEX else self.background_can_run(role)

    def request_background_index_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = str(payload.get("root") or "").strip()
        if not root:
            return {"ok": False, "error": "missing index search root"}
        return self.search_indexer.search(root, str(payload.get("query") or ""), int(payload.get("limit") or 400))

    def background_owner_status_payload(self) -> tuple[dict[str, Any], HTTPStatus]:
        # This path is polled by the topbar.  Diagnostics have a bounded, explicit admin
        # endpoint so routine owner state never serializes the recent profiling ring.
        return self.background_owner.status_payload(), HTTPStatus.OK

    def performance_diagnostics_payload(self) -> dict[str, Any]:
        """Return bounded profiling summaries without making status polling expensive."""

        metrics = self.performance_metrics_payload()
        phase_rows = [
            dict(row)
            for row in metrics.get("summary", [])
            if isinstance(row, dict) and str(row.get("surface") or "").startswith("phase:")
        ]
        repeated_work = []
        for row in metrics.get("summary", []):
            if not isinstance(row, dict):
                continue
            cache = row.get("cache")
            if not isinstance(cache, dict):
                continue
            avoided_recomputes = sum(
                max(0, int(value or 0))
                for status, value in cache.items()
                if str(status).startswith("hit") or str(status) == "coalesced"
            )
            if avoided_recomputes:
                repeated_work.append({
                    "role": str(row.get("role") or ""),
                    "surface": str(row.get("surface") or ""),
                    "avoided_recomputes": avoided_recomputes,
                })
        repeated_work.sort(key=lambda row: (-int(row["avoided_recomputes"]), row["role"], row["surface"]))
        return {
            "perf": metrics,
            "shared_phase_counters": phase_rows[:64],
            "repeated_work": repeated_work[:64],
        }

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
            diagnostic = str(status_payload.get("last_error") or "background owner takeover failed")
            payload.update(user_message_payload("common.requestFailed", diagnostic))
            payload["diagnostic"] = diagnostic
            return payload, HTTPStatus.CONFLICT
        return payload, HTTPStatus.OK

    def demote_background_owner(self) -> None:
        self.pricing_refresh_coordinator.stop_periodic()
        self.stats_current_runtime.stop()
        with self.metadata_warm_lock:
            self.metadata_warm_record.stop_event.set()
        with self.activity_transcript_service.tabber_cache_lock:
            demoted_warmer = self.activity_transcript_service.tabber_warmer_record
            self.activity_transcript_service.tabber_warmer_record = TabberActivityWarmerRecord()
            self.activity_transcript_service.tabber_cache_record.refresh_worker = None
        demoted_warmer.wake.set()  # unpark so a parked warmer exits promptly
        self.session_files_service.cancel_all_work()
        file_index.clear_memory_indexes()
        self.publish_client_event("background_owner_changed", self.background_owner.status_payload(), trigger="background-owner", cache="ready")

    def background_release_owner(self, requester: dict[str, Any]) -> dict[str, Any]:
        try:
            requester_priority = int(requester.get("priority") or 0)
        except (TypeError, ValueError):
            requester_priority = 0
        owner_priority = int(getattr(self.background_owner, "priority", 0) or 0)
        if self.background_owner.is_owner() and requester_priority < owner_priority:
            return {
                "ok": False,
                "owner": True,
                "error": "lower-priority server cannot release the preferred background owner",
                "status": self.background_owner.status_payload(),
            }
        was_owner = self.background_owner.is_owner()
        self.background_owner.release_owner("control_release")
        if was_owner:
            self.log_event(
                None,
                "background_owner_released",
                "Background owner released for another server",
                {"requester": requester},
                message_key="events.message.backgroundOwner.released",
            )
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
        self.log_event(
            None,
            "background_refresh_fallback",
            "Background owner refresh fallback engaged",
            {"role": role, "result": result, "payload": payload or {}},
            message_key="events.message.backgroundOwner.refreshFallback",
        )

    def request_background_refresh(self, role: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        request_payload = payload or {}
        if hasattr(self.background_owner, "request_owner_refresh"):
            result = self.background_owner.request_owner_refresh(role, request_payload)
        else:
            self.background_owner.record_refresh_request(role)
            result = {"ok": False, "accepted": False, "role": role, "fallback": False}
        if result.get("local_owner") and not result.get("coalesced") and role == BACKGROUND_ROLE_SEARCH_INDEX:
            root = str(request_payload.get("root") or "").strip()
            if root:
                try:
                    if request_payload.get("operation") == "unindex":
                        result["indexer"] = self.search_indexer.unindex(root)
                    else:
                        changed_paths = request_payload.get("paths")
                        if not isinstance(changed_paths, list):
                            changed_paths = [request_payload.get("path")] if request_payload.get("path") else []
                        normalized_changed_paths = [str(path) for path in changed_paths if isinstance(path, str) and path]
                        result["indexer"] = self.search_indexer.enqueue(
                            root,
                            normalized_changed_paths,
                            reason=str(request_payload.get("reason") or "owner-refresh"),
                        )
                    if not result["indexer"].get("accepted"):
                        result.update({
                            "ok": False,
                            "accepted": False,
                            "error": str(result["indexer"].get("error") or "persistent indexer unavailable"),
                        })
                except filesystem.FilesystemError as exc:
                    result.update({"ok": False, "accepted": False, "error": str(exc)})
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
            if role == BACKGROUND_ROLE_STATS_SAMPLER and request_payload.get("family") == "agent_tokens":
                result["refreshing"] = self.stats_current_runtime.wake("agent_tokens")
            if not result.get("coalesced"):
                self.log_sampled_background_refresh_event(
                    "background_refresh_started",
                    role,
                    "Background refresh accepted by local owner",
                    self.background_refresh_event_details(role, request_payload, extra={"source": "owner-request"}),
                    message_key="events.message.backgroundRefresh.accepted",
                )
                if role == BACKGROUND_ROLE_SESSION_FILES and ("session" in request_payload or "cache_key_data" in request_payload):
                    result["refreshing"] = self.start_requested_session_files_cache_refresh(request_payload)
                elif role == BACKGROUND_ROLE_TABBER_ACTIVITY:
                    result["refreshing"] = self.start_tabber_activity_cache_refresh()
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
            self.yoagent_controller.prune_yoagent_session_summaries(set(sessions))
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
            "locale": set(LANGUAGE_PREFERENCES - {"system"}),
            "languagePref": set(LANGUAGE_PREFERENCES),
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
            diagnostic = f"unknown session: {bad_session}"
            return {
                "session": bad_session,
                **user_message_payload("share.error.unknownSession", diagnostic, session=bad_session),
            }, HTTPStatus.NOT_FOUND
        if not share_sessions:
            diagnostic = "at least one tmux session is required"
            return {"session": "", **user_message_payload("share.error.sessionRequired", diagnostic)}, HTTPStatus.BAD_REQUEST
        primary_session = share_sessions[0]
        bounded_ttl = self.bounded_share_ttl_seconds(ttl_seconds)
        if bounded_ttl is None:
            diagnostic = "ttl must be a positive number of seconds"
            return {
                "session": primary_session,
                "sessions": share_sessions,
                **user_message_payload("share.error.ttlPositive", diagnostic),
            }, HTTPStatus.BAD_REQUEST
        bounded_viewers = self.bounded_share_max_viewers(max_viewers)
        if bounded_viewers is None:
            diagnostic = "max_viewers must be a positive integer"
            return {
                "session": primary_session,
                "sessions": share_sessions,
                **user_message_payload("share.error.maxViewersPositive", diagnostic),
            }, HTTPStatus.BAD_REQUEST
        requested_mode = self.normalize_share_mode(mode, read_only=read_only)
        requested_scheme = self.normalize_share_scheme(scheme, base_url=base_url)
        if not tls_available:
            share_mode = "ro"
            share_scheme = "http"
        elif requested_mode == "rw":
            if requested_scheme != "https" or not request_is_https:
                diagnostic = "write shares require https"
                return {
                    "session": primary_session,
                    "sessions": share_sessions,
                    **user_message_payload("share.error.writeHttps", diagnostic),
                }, HTTPStatus.BAD_REQUEST
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
            diagnostic = "share token expired or revoked"
            return {
                "ok": False,
                "active": False,
                **user_message_payload("share.error.tokenExpired", diagnostic),
            }, HTTPStatus.UNAUTHORIZED
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
            diagnostic = "share token required"
            return {"ok": False, **user_message_payload("share.error.tokenRequired", diagnostic)}, HTTPStatus.UNAUTHORIZED
        if not isinstance(payload, dict):
            diagnostic = "debug profile payload must be an object"
            return {"ok": False, **user_message_payload("share.error.debugProfileObject", diagnostic)}, HTTPStatus.BAD_REQUEST
        now = time.time()
        clean_payload = self.redact_share_debug_profile_value(payload)
        event: dict[str, Any] | None = None
        with self.share_tokens_lock:
            self.prune_inactive_share_tokens_locked(now)
            for stored_token, record in self.share_tokens.items():
                if not hmac.compare_digest(stored_token, raw_token):
                    continue
                if record.get("revoked") or not self.share_record_sessions_are_active(record):
                    diagnostic = "share token expired or revoked"
                    return {"ok": False, **user_message_payload("share.error.tokenExpired", diagnostic)}, HTTPStatus.UNAUTHORIZED
                if not bool(record.get("debug_profile")):
                    diagnostic = "debug/profiling upload is not enabled for this share"
                    return {"ok": False, **user_message_payload("share.error.debugProfileDisabled", diagnostic)}, HTTPStatus.FORBIDDEN
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
            diagnostic = "share token expired or revoked"
            return {"ok": False, **user_message_payload("share.error.tokenExpired", diagnostic)}, HTTPStatus.UNAUTHORIZED
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
            diagnostic = "share token or id required"
            return {"ok": False, **user_message_payload("share.error.tokenOrIdRequired", diagnostic)}, HTTPStatus.BAD_REQUEST
        bounded_add = self.bounded_share_ttl_seconds(add_seconds)
        if bounded_add is None:
            diagnostic = "extension must be a positive number of seconds"
            return {"ok": False, **user_message_payload("share.error.extensionPositive", diagnostic)}, HTTPStatus.BAD_REQUEST
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
        diagnostic = "share token expired or revoked"
        return {"ok": False, **user_message_payload("share.error.tokenExpired", diagnostic)}, HTTPStatus.NOT_FOUND

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
            diagnostic = "share token required"
            return user_message_payload("share.error.tokenRequired", diagnostic), HTTPStatus.UNAUTHORIZED
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
                    diagnostic = "share token is scoped to a different session"
                    return user_message_payload("share.error.sessionScope", diagnostic), HTTPStatus.FORBIDDEN
                if record.get("revoked") or not self.share_record_sessions_are_active(record):
                    diagnostic = "share token expired or revoked"
                    return user_message_payload("share.error.tokenExpired", diagnostic), HTTPStatus.UNAUTHORIZED
                viewer_ids = self.share_record_viewer_ids(record)
                viewers = len(viewer_ids)
                max_viewers = int(record.get("max_viewers") or 0)
                if clean_viewer_id not in viewer_ids and max_viewers > 0 and viewers >= max_viewers:
                    diagnostic = "share viewer limit reached"
                    return user_message_payload("share.error.viewerLimitReached", diagnostic), HTTPStatus.FORBIDDEN
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
        diagnostic = "share token expired or revoked"
        return user_message_payload("share.error.tokenExpired", diagnostic), HTTPStatus.UNAUTHORIZED

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
        status = self.approval_client.service_status()
        targets = status.get("targets") if isinstance(status.get("targets"), list) else []
        local_enabled = {
            str(item.get("session") or "")
            for item in targets
            if isinstance(item, dict) and item.get("enabled") is True
        }
        local_enabled = {session for session in local_enabled if session in self.sessions}
        current = read_yolomux_state().get("auto_approve_enabled", [])
        if isinstance(current, list):
            external_enabled = {
                session
                for session in current
                if isinstance(session, str) and session not in local_enabled and self.auto_approve_session_lock_owner(session)
            }
        else:
            external_enabled = set()
        update_yolomux_state({"auto_approve_enabled": sorted(local_enabled | external_enabled)})

    def notify_status(self) -> dict[str, Any]:
        return {"enabled": bool(read_yolomux_state().get("notify_enabled", False))}

    def settings_payload(self) -> dict[str, Any]:
        return settings_payload()

    def chat_retention_days(self) -> int:
        value = self.settings_payload().get("settings", {}).get("chat", {}).get("retention_days", 7)
        try:
            return max(1, min(365, int(value)))
        except (TypeError, ValueError):
            return 7

    def chat_bootstrap(self, username: str, browser_instance_id: Any) -> dict[str, Any]:
        return self.chat_service.bootstrap(username=username, browser_instance_id=browser_instance_id)

    def chat_page(self, username: str, **kwargs: Any) -> dict[str, Any]:
        return self.chat_service.page(username=username, **kwargs)

    def chat_delta(self, username: str, **kwargs: Any) -> dict[str, Any]:
        return self.chat_service.delta(username=username, **kwargs)

    def chat_context(self, username: str, **kwargs: Any) -> dict[str, Any]:
        return self.chat_service.context(username=username, **kwargs)

    def chat_search(self, username: str, **kwargs: Any) -> dict[str, Any]:
        return self.chat_service.search(username=username, **kwargs)

    def chat_send(self, username: str, payload: dict[str, Any], locale: str, sender_ip: str = "") -> dict[str, Any]:
        result, created = self.chat_service.send(username=username, sender_ip=sender_ip, payload=payload, locale=locale)
        if created:
            self.publish_background_client_event(
                "chat_messages_changed",
                {"revision": result["revision"], "message_id": result["message"]["id"]},
                trigger="chat-send",
                cache="ready",
            )
        return result

    def chat_yoagent(self, username: str, access_role: str, payload: dict[str, Any], locale: str) -> dict[str, Any]:
        source, query = self.chat_service.yoagent_source(
            username=username,
            browser_instance_id=payload.get("browser_instance_id"),
            message_id=payload.get("message_id"),
        )
        typing_instance_id = f"{CHAT_YOAGENT_INSTANCE_ID}-{source.id}"
        typing_stop = threading.Event()
        self.chat_typing(CHAT_YOAGENT_USERNAME, typing_instance_id, True)

        def refresh_typing() -> None:
            while not typing_stop.wait(CHAT_TYPING_LEASE_SECONDS / 2):
                self.chat_typing(CHAT_YOAGENT_USERNAME, typing_instance_id, True)

        typing_thread = threading.Thread(target=refresh_typing, name=f"yochat-typing-{source.id}", daemon=True)
        typing_thread.start()
        try:
            response, _status = self.yoagent_controller.yoagent_chat(
                {"message": query, "locale": locale, "request_id": f"yochat-{source.id}"},
                access_role=access_role,
            )
        finally:
            typing_stop.set()
            typing_thread.join()
            self.chat_typing(CHAT_YOAGENT_USERNAME, typing_instance_id, False)
        descriptor = response.get("user_message") if isinstance(response.get("user_message"), dict) else {}
        answer = str(response.get("answer") or descriptor.get("fallback") or response.get("error") or "").strip()
        result, created = self.chat_service.record_yoagent_reply(source=source, answer=answer)
        if created:
            self.publish_background_client_event(
                "chat_messages_changed",
                {"revision": result["revision"], "message_id": result["message"]["id"]},
                trigger="chat-yoagent",
                cache="ready",
            )
        return {**result, "source_message_id": source.id}

    def chat_typing(self, username: str, browser_instance_id: Any, typing: Any) -> dict[str, Any]:
        result = self.chat_service.typing(username=username, browser_instance_id=browser_instance_id, typing=typing)
        self.publish_background_client_event(
            "chat_typing_changed",
            {"revision": time.time_ns()},
            trigger="chat-typing",
            cache="ready",
        )
        return result

    def chat_read(self, username: str, message_id: Any) -> dict[str, Any]:
        return self.chat_service.read(username=username, message_id=message_id)

    def summary_settings(self) -> dict[str, Any]:
        return normalized_summary_settings(self.settings_payload().get("settings"))

    def pricing_catalog_status_payload(self) -> dict[str, Any]:
        """Return local catalog state; this never starts a provider fetch."""
        return {
            "catalog": self.pricing_catalog.public_payload(),
            "refresh": self.pricing_refresh_coordinator.status(),
        }

    def pricing_catalog_refresh_start(self) -> dict[str, Any]:
        """Start the explicit bounded Refresh worker and return immediately."""
        return self.pricing_refresh_coordinator.start()

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
            with self.attention_ack_lock:
                previous_keys = set(self.attention_ack_keys)
            if not self.merge_shared_attention_acks():
                return {"ok": True, "accepted": True, "noop": True}
            self.invalidate_auto_approve_cache()
            raw_payload = request.get("payload")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            raw_acknowledged = payload.get("acknowledged") if isinstance(payload.get("acknowledged"), list) else []
            with self.attention_ack_lock:
                current_keys = set(self.attention_ack_keys)
                payload_keys = {str(key) for key in raw_acknowledged if str(key) in current_keys}
                acknowledged = sorted(payload_keys | (current_keys - previous_keys))
                acknowledged_at = {key: self.attention_ack_keys[key] for key in acknowledged}
            self.publish_client_event(
                "attention_acks_changed",
                {"acknowledged": acknowledged, "acknowledged_at": acknowledged_at},
                trigger="background-fanout",
                cache="ready",
            )
            return {"ok": True, "accepted": True, "event": {"type": event_type}}
        if event_type == "auto_approve_changed":
            # The worker records are process-local, but every status response is cached. A
            # follower must discard that cache before it tells its SSE clients to refresh.
            self.invalidate_auto_approve_cache()
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

    def work_graph_refresh_signature(self, graph: dict[str, Any]) -> str:
        """Compare graph content without treating its per-build ordering token as a change."""
        return self.stable_client_event_payload_signature({key: value for key, value in graph.items() if key != "generation"})

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

    def session_files_max_workers(self) -> int:
        """Return the bounded cold-rebuild queue width, not a request fan-out width."""
        default = SESSION_FILES_BATCH_MAX_WORKERS
        settings = settings_payload().get("settings", {})
        performance = settings.get("performance", {}) if isinstance(settings, dict) else {}
        value = performance.get("session_files_max_workers", default) if isinstance(performance, dict) else default
        return max(1, min(8, int(self.float_value(value, default))))

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
        return self.stable_client_event_signature_payload(payload)

    def recent_tmux_signal_removal_event(self, generated_at: Any = None) -> dict[str, Any]:
        with self.client_watch_service.lock:
            event = dict(self.client_watch_service.tmux_signal_removal_event)
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
        plan = ["git pull --ff-only origin main", "install or download xterm assets", "python3 tools/static_build.py", "restart server"]
        if dryrun:
            diagnostic = "dryrun: nothing pulled, server not restarted"
            return {
                "ok": True,
                "dryrun": True,
                "restarting": False,
                "plan": plan,
                **user_message_payload("update.result.dryRun", diagnostic),
            }
        pull = common.git(["pull", "--ff-only", "origin", "main"], root)
        if pull.returncode != 0:
            # Never force: a dirty/diverged ("read-only") checkout must not be clobbered.
            diagnostic = (pull.stderr or "git pull --ff-only failed").strip()[:400]
            return {
                "ok": False,
                "dryrun": False,
                "restarting": False,
                "plan": plan,
                **user_message_payload("update.result.blocked", diagnostic),
            }
        assets_ready, assets_error = ensure_xterm_runtime_assets(root)
        if not assets_ready:
            return {
                "ok": False,
                "dryrun": False,
                "restarting": False,
                "plan": plan,
                **user_message_payload("update.result.assetsUnavailable", assets_error),
            }
        try:
            static_build = subprocess.run(
                ["python3", "tools/static_build.py"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            diagnostic = f"static build failed: {exc}"[:400]
            return {
                "ok": False,
                "dryrun": False,
                "restarting": False,
                "plan": plan,
                **user_message_payload("update.result.blocked", diagnostic),
            }
        if static_build.returncode != 0:
            build_error = (static_build.stderr or static_build.stdout or "static build failed").strip()[:360]
            diagnostic = f"static build failed: {build_error}"[:400]
            return {
                "ok": False,
                "dryrun": False,
                "restarting": False,
                "plan": plan,
                **user_message_payload("update.result.blocked", diagnostic),
            }
        restarting = self._spawn_self_restart()
        diagnostic = "updated; restarting now" if restarting else "updated; restart spawn failed; restart the server manually"
        key = "update.result.restarting" if restarting else "update.result.restartFailed"
        return {
            "ok": True,
            "dryrun": False,
            "restarting": restarting,
            "plan": plan,
            **user_message_payload(key, diagnostic),
        }

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

    def publish_update_notification_if_available(self) -> None:
        status = self.update_status_payload(dryrun=False)
        target = status.get("target")
        if status.get("available") and status.get("notify") and target and target != self._update_last_target:
            self._update_last_target = target
            self.publish_client_event("update_available", status, trigger="update-check")

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
                self.publish_update_notification_if_available()
            except Exception as exc:
                logging.exception("update check failed: %s", exc)
            interval_minutes = section.get("check_interval_minutes", 60)
            try:
                interval = max(1.0, float(interval_minutes)) * 60.0
            except (TypeError, ValueError):
                interval = 3600.0
            time.sleep(interval)

    def start_update_check_thread(self) -> bool:
        if self.update_check_thread is not None:
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
        with self.activity_transcript_service.tabber_cache_lock:
            record = self.activity_transcript_service.tabber_warmer_record
            record.consumer_until = max(record.consumer_until, until)
        # Unpark a warmer that idled out of demand; a no-op while it is active.
        record.wake.set()
        return True

    def tabber_activity_has_recent_consumer(self) -> bool:
        now = time.monotonic()
        with self.activity_transcript_service.tabber_cache_lock:
            return self.activity_transcript_service.tabber_warmer_record.consumer_until > now

    def wake_client_event_watcher(self) -> None:
        with self.client_watch_service.lock:
            record = self.client_watch_service.event_watcher_record
        record.wake_event.set()

    def client_event_watch_sleep_seconds(self, now: float, record: ClientEventWatcherRecord | None = None) -> float:
        current = record or self.client_watch_service.event_watcher_record
        channels = self.client_events.aggregate_channels()
        deadlines: list[float] = []
        if not channels.isdisjoint({"files", "transcripts", "activity"}):
            deadlines.extend((current.next_signature_poll_at, current.next_file_poll_at, current.next_background_file_poll_at))
            if not current.filesystem_healthy:
                deadlines.append(current.next_filesystem_retry_at)
        if not channels.isdisjoint({"status", "attention"}):
            deadlines.extend((current.next_auto_poll_at, current.next_attention_ack_poll_at, current.next_tmux_signal_poll_at))
        if not channels.isdisjoint({"core", "attention"}):
            deadlines.append(current.next_watched_pr_poll_at)
        if not channels.isdisjoint({"yoagent", "attention"}):
            deadlines.append(current.next_yoagent_job_poll_at)
        if not deadlines:
            return 60.0
        next_due = min(deadlines)
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
        with self.client_watch_service.lock:
            expires_at = now + CLIENT_WATCH_ROOT_TTL_SECONDS
            self.client_watch_service.file_records = {
                **{path: ClientWatchFileRecord(expires_at=expires_at, background=False) for path in unique_files},
                **{path: ClientWatchFileRecord(expires_at=expires_at, background=True) for path in unique_background_files},
            }
            self.client_watch_service.context_items = context_items
            self.client_watch_service.session_files = session_files_requests
            self.client_watch_service.activity_summary = activity_summary
        self.wake_client_event_watcher()
        self.request_native_filesystem_watch_reconfigure()
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
        locale = normalize_locale(value.get("locale"))
        visible = value.get("visible") is True
        scope = self.normalized_activity_session_scope(value.get("scope"))
        hours = session_files.bounded_session_files_hours(self.float_value(value.get("hours"), 24.0))
        return {"locale": locale, "visible": visible, "scope": scope, "hours": hours}

    def client_watch_roots_snapshot(self) -> list[str]:
        return self.watch_root_index.snapshot()

    def client_watch_file_paths(self, *, background: bool) -> list[str]:
        now = time.monotonic()
        with self.client_watch_service.lock:
            expired = [path for path, record in self.client_watch_service.file_records.items() if record.expires_at <= now]
            for path in expired:
                self.client_watch_service.file_records.pop(path, None)
            return sorted(path for path, record in self.client_watch_service.file_records.items() if record.background is background)

    def client_watch_files_snapshot(self) -> list[str]:
        return self.client_watch_file_paths(background=False)

    def client_watch_background_files_snapshot(self) -> list[str]:
        return self.client_watch_file_paths(background=True)

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
        # Keep active-session discovery for its metadata consumers, but do not
        # turn every session cwd or configured companion into a hot filesystem
        # watch. High-frequency observation belongs only to roots the browser
        # currently displays in Finder or Differ.
        self.watch_root_index.update_active_roots(self.active_directory_watch_roots(sessions))
        roots = set(self.client_watch_roots_snapshot())
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
        changed = self.watch_root_index.publish_signature_snapshot(signature)
        self.record_performance_sample(
            BACKGROUND_ROLE_WATCH_ROOTS,
            "watch-signature",
            trigger="poll",
            compute_ms=(time.perf_counter() - started) * 1000,
            payload={"roots": roots, "signature_count": len(signature)},
            cache_status="changed" if changed else "unchanged",
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
        shared_signature = self.watch_root_index.signature_snapshot()
        if shared_signature:
            return shared_signature
        with self.client_watch_service.lock:
            return self.client_watch_service.filesystem_signature or (("watch-roots", "follower"),)

    def files_for_watch(self) -> list[str]:
        return self.client_watch_files_snapshot()[:CLIENT_WATCH_FILE_LIMIT]

    def files_watch_signature(self) -> tuple[Any, ...]:
        return tuple((path, file_watch_signature(path)) for path in self.files_for_watch())

    def background_files_for_watch(self) -> list[str]:
        return self.client_watch_background_files_snapshot()[:CLIENT_WATCH_FILE_LIMIT]

    def background_files_watch_signature(self) -> tuple[Any, ...]:
        return tuple((path, file_watch_signature(path)) for path in self.background_files_for_watch())

    def native_filesystem_watching_supported(self) -> bool:
        return watchfiles_watch is not None

    @staticmethod
    def compact_native_filesystem_watch_paths(paths: list[str]) -> tuple[str, ...]:
        """Watch the smallest non-overlapping set of roots."""
        candidates = sorted(
            {str(Path(path).expanduser().resolve(strict=False)) for path in paths if str(path or "").startswith("/")},
            key=lambda item: (len(Path(item).parts), item),
        )
        compacted: list[str] = []
        for candidate in candidates:
            candidate_path = Path(candidate)
            if any(filesystem._path_is_within(candidate_path, Path(parent)) for parent in compacted):
                continue
            compacted.append(candidate)
        return tuple(compacted)

    def native_filesystem_watch_configuration(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], frozenset[str]]:
        """Return demanded filesystem roots plus exact settings/transcript/file parents."""
        sessions, _errors = discover_sessions(self.sessions)
        # watchfiles reports canonical paths on macOS (for example `/private/tmp`
        # for a `/tmp` root), so keep the index/event roots canonical too.
        roots = tuple(sorted({
            str(Path(root).expanduser().resolve(strict=False))
            for root in self.filesystem_roots_for_watch(sessions)
        }))
        transcripts = tuple(sorted({
            str(Path(agent.transcript).expanduser().resolve(strict=False))
            for info in sessions.values()
            for agent in info.agents
            if agent.transcript
        }))
        files = [*self.files_for_watch(), *self.background_files_for_watch()]
        watch_paths = [*roots, str(SETTINGS_PATH.parent), *(str(Path(path).expanduser().parent) for path in files), *(str(Path(path).parent) for path in transcripts)]
        settings = settings_payload().get("settings", {})
        file_explorer = settings.get("file_explorer", {}) if isinstance(settings, dict) else {}
        skip_dirs = filesystem.search._configured_search_skip_dirs(file_explorer if isinstance(file_explorer, dict) else {})
        return roots, self.compact_native_filesystem_watch_paths(watch_paths), transcripts, frozenset(skip_dirs)

    def request_native_filesystem_watch_reconfigure(self) -> None:
        with self.client_watch_service.lock:
            record = self.client_watch_service.event_watcher_record
            if record.filesystem_worker is None:
                return
            record.filesystem_reconfigure_event.set()
            record.filesystem_stop_event.set()
            record.wake_event.set()

    GIT_METADATA_EVENT_NAMES = frozenset({"HEAD", "index", "packed-refs", "MERGE_HEAD"})

    def git_metadata_event_allowed(self, path: Path, record: ClientEventWatcherRecord) -> bool:
        """Admit exactly the .git paths that change `git_snapshot_identity`.

        Ref updates and commits touch only .git (HEAD, index, refs, packed-refs,
        MERGE_HEAD); without these events a pure commit would never dirty the
        repository record. objects/ and logs/ churn stays filtered out.
        """
        parts = path.parts
        try:
            git_index = parts.index(".git")
        except ValueError:
            return False
        tail = parts[git_index + 1:]
        if not tail or not (tail[0] in self.GIT_METADATA_EVENT_NAMES or tail[0] == "refs"):
            return False
        return any(
            path == Path(root) or filesystem._path_is_within(path, Path(root))
            for root in record.filesystem_watch_paths
        )

    def native_filesystem_event_allowed(self, path: Path, record: ClientEventWatcherRecord) -> bool:
        if ".git" in path.parts:
            return self.git_metadata_event_allowed(path, record)
        if any(part in record.filesystem_skip_dirs for part in path.parts):
            return False
        # Native backends report every change under an ancestor watch root,
        # including credentials we deliberately never allow filesystem APIs to
        # inspect.  Apply that same policy before a batch can reach indexing.
        try:
            filesystem._ensure_path_allowed(path)
        except filesystem.FilesystemError:
            return False
        return any(
            path == Path(root) or filesystem._path_is_within(path, Path(root))
            for root in record.filesystem_watch_paths
        )

    @staticmethod
    def native_filesystem_paths_intersect(left: Path, right: Path) -> bool:
        return (
            left == right
            or filesystem._path_is_within(left, right)
            or filesystem._path_is_within(right, left)
        )

    @staticmethod
    def native_filesystem_path_requires_reindex(change: Any, path: Path, roots: tuple[str, ...] = ()) -> bool:
        """Ignore directory-metadata notifications when invalidating Quick Open.

        Native backends commonly report a modified watch root in addition to the
        actual file that changed below it.  Reindexing that root would turn one
        edit into a full subtree walk.  A file event, or an added/deleted
        directory, still needs normal incremental handling.
        """
        try:
            change_code = int(change)
        except (TypeError, ValueError):
            change_code = 0
        if change_code == 2 and path.is_dir():  # watchfiles.Change.modified
            return False
        # A watch root is already present when registration succeeds. Native
        # backends can report that root as added, deleted, or modified while
        # reconciling their own cursor; treating any of those as a dirty
        # subtree rewalks the entire index. Real descendant events are still
        # delivered, and the periodic reconciliation remains the backstop.
        return not any(path == Path(root) for root in roots)

    def publish_native_files_changed(self, changed_paths: list[Path]) -> list[str]:
        watched_files = [*self.files_for_watch(), *self.background_files_for_watch()]
        changed: list[dict[str, Any]] = []
        for raw_path in sorted(set(watched_files)):
            path = Path(raw_path).expanduser().resolve(strict=False)
            if not any(self.native_filesystem_paths_intersect(path, changed_path) for changed_path in changed_paths):
                continue
            changed.append({"path": str(path), "signature": file_watch_signature(path)})
        if not changed:
            return []
        self.publish_client_event(
            "files_changed",
            {"files": changed, "count": len(changed)},
            trigger="native-watch",
            cache="ready",
        )
        return ["files_changed"]

    def handle_native_filesystem_changes(
        self,
        record: ClientEventWatcherRecord,
        changes: set[tuple[Any, str]],
    ) -> list[str]:
        """Route one debounced native event batch through existing event owners."""
        with self.client_watch_service.lock:
            if self.client_watch_service.event_watcher_record is not record or record.stop_event.is_set():
                return []
            roots = tuple(record.filesystem_roots)
            transcripts = tuple(record.filesystem_transcripts)
        native_changes: list[tuple[Any, Path]] = []
        for change, raw_path in changes:
            if not isinstance(raw_path, str) or not raw_path.startswith("/"):
                continue
            path = Path(raw_path).expanduser().resolve(strict=False)
            if self.native_filesystem_event_allowed(path, record):
                native_changes.append((change, path))
        all_changed = sorted({path for _change, path in native_changes}, key=str)
        if not all_changed:
            return []
        # Every change (worktree or .git metadata) dirties intersecting repo
        # records; .git paths take part in NOTHING else below.
        self.mark_repo_state_dirty(all_changed)
        changed_paths = [path for path in all_changed if ".git" not in path.parts]
        if not changed_paths:
            return []
        events = self.publish_native_files_changed(changed_paths)
        settings_path = SETTINGS_PATH.expanduser().resolve(strict=False)
        if any(self.native_filesystem_paths_intersect(settings_path, path) for path in changed_paths):
            settings_signature = self.settings_watch_signature()
            with self.client_watch_service.lock:
                previous = self.client_watch_service.settings_signature
                self.client_watch_service.settings_signature = settings_signature
            if previous and previous != settings_signature:
                self.publish_client_event(
                    "settings_changed",
                    {"signature": settings_signature, "data": self.settings_payload()},
                    trigger="native-watch",
                    cache="ready",
                )
                events.append("settings_changed")
        transcript_paths = [Path(path) for path in transcripts]
        if transcript_paths and any(
            self.native_filesystem_paths_intersect(transcript, path)
            for transcript in transcript_paths
            for path in changed_paths
        ):
            self.clear_transcript_caches()
            self.publish_client_event(
                "transcripts_changed",
                {"refresh": True},
                trigger="native-watch",
                cache="refresh",
            )
            events.append("transcripts_changed")
            events.extend(self.publish_context_items_ready_events(trigger="native-watch"))
            events.extend(self.publish_activity_summary_ready_events(trigger="native-watch"))
            events.extend(self.publish_session_files_ready_events(trigger="native-watch"))
        filesystem_event_paths = [
            path
            for path in changed_paths
            if any(path == Path(root) or filesystem._path_is_within(path, Path(root)) for root in roots)
        ]
        if not filesystem_event_paths:
            return events
        filesystem_paths = sorted({
            path
            for change, path in native_changes
            if self.native_filesystem_path_requires_reindex(change, path, roots)
            and any(
                path == Path(root)
                or filesystem._path_is_within(path, Path(root))
                for root in roots
            )
        }, key=str)
        if filesystem_paths:
            filesystem.reindex_roots_for_paths([str(path) for path in filesystem_paths], reason="native-watch")
        current_signature = self.filesystem_watch_signature_for_roots(list(roots))
        with self.client_watch_service.lock:
            previous_signature = self.client_watch_service.filesystem_signature
            self.client_watch_service.filesystem_signature = current_signature
        touched_roots = sorted({
            root
            for root in roots
            if any(path == Path(root) or filesystem._path_is_within(path, Path(root)) for path in filesystem_event_paths)
        })
        change_summary = {
            "roots_changed": len(touched_roots),
            "roots_added": 0,
            "roots_removed": 0,
            "event_paths": len(filesystem_event_paths),
            "indexed_paths": len(filesystem_paths),
        }
        if previous_signature != current_signature or touched_roots:
            events.extend(self.publish_filesystem_ready_event(
                list(roots),
                trigger="native-watch",
                change_summary=change_summary,
                current_signature=current_signature,
            ))
        events.extend(self.publish_session_files_ready_events(trigger="native-watch"))
        return events

    def start_native_filesystem_watcher(self, record: ClientEventWatcherRecord | None = None) -> bool:
        if not self.native_filesystem_watching_supported() or not self.background_can_run(BACKGROUND_ROLE_WATCH_ROOTS):
            return False
        with self.client_watch_service.lock:
            current = record or self.client_watch_service.event_watcher_record
            if self.client_watch_service.event_watcher_record is not current or current.stop_event.is_set():
                return False
            worker = current.filesystem_worker
            if worker is not None and worker.is_alive():
                return False
            worker = threading.Thread(target=self.native_filesystem_watch_loop, args=(current,), name="native-filesystem-watch", daemon=True)
            current.filesystem_worker = worker

        def rollback() -> None:
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is current and current.filesystem_worker is worker:
                    current.filesystem_worker = None
                    current.filesystem_healthy = False

        common.start_thread_with_rollback(worker, rollback)
        return True

    def native_filesystem_watch_loop(self, record: ClientEventWatcherRecord) -> None:
        worker = threading.current_thread()
        try:
            while not record.stop_event.is_set():
                roots, watch_paths, transcripts, skip_dirs = self.native_filesystem_watch_configuration()
                with self.client_watch_service.lock:
                    if self.client_watch_service.event_watcher_record is not record or record.stop_event.is_set():
                        return
                    record.filesystem_roots = roots
                    record.filesystem_watch_paths = watch_paths
                    record.filesystem_transcripts = transcripts
                    record.filesystem_skip_dirs = skip_dirs
                    record.filesystem_stop_event = threading.Event()
                    record.filesystem_reconfigure_event.clear()
                    stop_event = record.filesystem_stop_event
                if not watch_paths:
                    record.wake_event.wait(timeout=1.0)
                    record.wake_event.clear()
                    continue
                try:
                    with self.client_watch_service.lock:
                        record.filesystem_healthy = True
                    assert watchfiles_watch is not None
                    for changes in watchfiles_watch(
                        *watch_paths,
                        watch_filter=lambda _change, raw_path: self.native_filesystem_event_allowed(Path(raw_path).expanduser().resolve(strict=False), record),
                        debounce=NATIVE_FILESYSTEM_WATCH_DEBOUNCE_MS,
                        step=NATIVE_FILESYSTEM_WATCH_STEP_MS,
                        rust_timeout=NATIVE_FILESYSTEM_WATCH_RUST_TIMEOUT_MS,
                        stop_event=stop_event,
                        raise_interrupt=False,
                    ):
                        if record.stop_event.is_set() or stop_event.is_set():
                            break
                        if changes:
                            try:
                                self.handle_native_filesystem_changes(record, changes)
                            except Exception as exc:  # Keep one bad OS event from killing the watcher.
                                self.log_event(
                                    None,
                                    "native_filesystem_watch_event_error",
                                    f"native filesystem watch event failed: {exc}",
                                    {"diagnostic": str(exc)},
                                    message_key="events.message.clientEvent.directoryWatchFailed",
                                )
                except (OSError, RuntimeError, ValueError) as exc:
                    with self.client_watch_service.lock:
                        record.filesystem_healthy = False
                        record.next_filesystem_retry_at = time.monotonic() + NATIVE_FILESYSTEM_RETRY_SECONDS
                    self.log_event(
                        None,
                        "native_filesystem_watch_error",
                        f"native filesystem watch failed: {exc}",
                        {"diagnostic": str(exc)},
                        message_key="events.message.clientEvent.directoryWatchFailed",
                    )
                    if record.stop_event.wait(NATIVE_FILESYSTEM_RETRY_SECONDS):
                        return
                if not record.filesystem_reconfigure_event.is_set() and not record.stop_event.is_set():
                    # A backend that returns without an error is treated like a transient
                    # watch loss; retry after the same bounded backoff.
                    with self.client_watch_service.lock:
                        record.filesystem_healthy = False
                        record.next_filesystem_retry_at = time.monotonic() + NATIVE_FILESYSTEM_RETRY_SECONDS
                    if record.stop_event.wait(NATIVE_FILESYSTEM_RETRY_SECONDS):
                        return
        finally:
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is record and record.filesystem_worker is worker:
                    record.filesystem_worker = None
                    record.filesystem_healthy = False

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
        with self.client_watch_service.lock:
            if self.client_watch_service.filesystem_history and self.client_watch_service.filesystem_history[-1]["signature"] == signature:
                return str(self.client_watch_service.filesystem_history[-1]["token"])
            signature_text = self.client_event_payload_signature(signature)
            digest = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:16]
            token = f"{int(now * 1000)}-{digest}"
            self.client_watch_service.filesystem_history.append({
                "token": token,
                "created_at": now,
                "signature": copy.deepcopy(signature),
            })
            min_created_at = now - FILESYSTEM_WATCH_HISTORY_SECONDS
            self.client_watch_service.filesystem_history = [
                record
                for record in self.client_watch_service.filesystem_history[-FILESYSTEM_WATCH_HISTORY_LIMIT:]
                if float(record.get("created_at") or 0.0) >= min_created_at
            ]
            return token

    def filesystem_watch_record_for_token(self, token: str) -> dict[str, Any] | None:
        clean_token = str(token or "").strip()
        if not clean_token:
            return None
        with self.client_watch_service.lock:
            for record in self.client_watch_service.filesystem_history:
                if record.get("token") == clean_token:
                    return copy.deepcopy(record)
        return None

    def latest_filesystem_watch_record(self, refresh: bool = False) -> dict[str, Any] | None:
        with self.client_watch_service.lock:
            if self.client_watch_service.filesystem_history and not refresh:
                return copy.deepcopy(self.client_watch_service.filesystem_history[-1])
        sessions, _errors = discover_sessions(self.sessions)
        if self.background_can_run(BACKGROUND_ROLE_WATCH_ROOTS):
            signature = self.filesystem_roots_watch_signature(sessions)
        else:
            signature = self.follower_filesystem_roots_watch_signature(sessions)
        if not signature:
            return None
        token = self.record_filesystem_watch_snapshot(signature)
        return self.filesystem_watch_record_for_token(token)

    def filesystem_watch_signature_for_roots(self, roots: list[str]) -> tuple[Any, ...]:
        return tuple((root, filesystem_watch_signature(root)) for root in roots[:CLIENT_WATCH_ROOT_LIMIT])

    def filesystem_watch_full_due(self) -> bool:
        with self.client_watch_service.lock:
            return self.client_watch_service.filesystem_last_full_at <= 0.0 or time.monotonic() - self.client_watch_service.filesystem_last_full_at >= FILESYSTEM_WATCH_KEYFRAME_SECONDS

    def mark_filesystem_watch_full_sent(self) -> None:
        with self.client_watch_service.lock:
            self.client_watch_service.filesystem_last_full_at = time.monotonic()

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
        with self.client_watch_service.lock:
            previous_signature = self.client_watch_service.filesystem_payload_signature
            self.client_watch_service.filesystem_payload_signature = token
        if previous_signature == token:
            return []
        full = force_full or trigger not in {"watch", "native-watch"} or self.filesystem_watch_full_due()
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
        with self.activity_transcript_service.transcript_tail_cache_lock:
            self.activity_transcript_service.transcript_tail_cache.clear()
        with self.activity_transcript_service.context_items_cache_lock:
            self.activity_transcript_service.context_items_cache.clear()

    def clear_transcript_caches(self) -> None:
        self.clear_transcript_content_caches()
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            record.generation += 1
            record.worker = None
            record.stored_at = None
            record.payload = None

    def start_client_watch_snapshot_publish(self) -> bool:
        generation = 0
        worker: threading.Thread | None = None
        with self.client_watch_service.lock:
            watcher_record = self.client_watch_service.event_watcher_record
            if watcher_record.snapshot_worker is not None:
                return False
            def run() -> None:
                self.publish_client_watch_snapshot(watcher_record, generation)

            worker = threading.Thread(target=run, daemon=True)
            watcher_record.snapshot_worker = worker
            generation = self.begin_transcripts_payload_work(worker, replace=True)
        try:
            worker.start()
        except RuntimeError:
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is watcher_record and watcher_record.snapshot_worker is worker:
                    watcher_record.snapshot_worker = None
            self.finish_transcripts_payload_work(generation, worker, invalidate=True)
            raise
        return True

    def client_watch_snapshot_is_current(self, record: ClientEventWatcherRecord, worker: threading.Thread) -> bool:
        with self.client_watch_service.lock:
            return (
                self.client_watch_service.event_watcher_record is record
                and record.snapshot_worker is worker
                and not record.stop_event.is_set()
            )

    def publish_client_watch_snapshot(
        self,
        record: ClientEventWatcherRecord | None = None,
        generation: int | None = None,
    ) -> None:
        worker = threading.current_thread()
        guarded = record is not None
        if generation is None:
            generation = self.begin_transcripts_payload_work(worker, replace=True)
        try:
            started = time.perf_counter()
            payload = self.build_transcripts_payload()
            if guarded and not self.client_watch_snapshot_is_current(record, worker):
                return
            if not self.commit_transcripts_payload_cache(payload, generation):
                return
            signature = self.transcripts_payload_event_signature(payload)
            with self.client_watch_service.lock:
                if guarded and (
                    self.client_watch_service.event_watcher_record is not record
                    or record.snapshot_worker is not worker
                    or record.stop_event.is_set()
                ):
                    return
                previous_signature = self.client_watch_service.transcripts_payload_signature
                self.client_watch_service.transcripts_payload_signature = signature
            if previous_signature != signature:
                self.publish_client_event(
                    "transcripts_changed",
                    {"signature": signature, "refresh": True},
                    trigger="watch_state",
                    cache="ready",
                    compute_ms=(time.perf_counter() - started) * 1000,
                )
            if guarded and not self.client_watch_snapshot_is_current(record, worker):
                return
            self.publish_context_items_ready_events(trigger="watch_state")
            if guarded and not self.client_watch_snapshot_is_current(record, worker):
                return
            self.publish_activity_summary_ready_events(trigger="watch_state")
            if guarded and not self.client_watch_snapshot_is_current(record, worker):
                return
            roots = self.client_watch_roots_snapshot()
            if self.background_can_run(BACKGROUND_ROLE_WATCH_ROOTS):
                self.publish_filesystem_ready_event(roots, trigger="watch_state")
            else:
                self.request_watch_roots_owner_refresh(roots, "watch_state")
            if guarded and not self.client_watch_snapshot_is_current(record, worker):
                return
            self.publish_session_files_ready_events(trigger="watch_state")
        finally:
            self.finish_transcripts_payload_work(generation, worker)
            with self.client_watch_service.lock:
                if guarded and self.client_watch_service.event_watcher_record is record and record.snapshot_worker is worker:
                    record.snapshot_worker = None

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
        with self.client_watch_service.lock:
            initialized = self.client_watch_service.initialized
            previous_filesystem_signature = self.client_watch_service.filesystem_signature
            settings_changed = initialized and self.client_watch_service.settings_signature != settings_signature
            transcripts_changed = initialized and self.client_watch_service.transcripts_signature != transcripts_signature
            transcript_content_changed = initialized and self.client_watch_service.transcript_content_signature != transcript_content_signature
            filesystem_changed = initialized and previous_filesystem_signature != filesystem_signature
            self.client_watch_service.initialized = True
            self.client_watch_service.settings_signature = settings_signature
            self.client_watch_service.transcripts_signature = transcripts_signature
            self.client_watch_service.transcript_content_signature = transcript_content_signature
            self.client_watch_service.filesystem_signature = filesystem_signature
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
            changed_paths = filesystem_changed_paths(previous_filesystem_signature, filesystem_signature)
            if changed_paths:
                filesystem.reindex_roots_for_paths(changed_paths, reason="fs-watch")
            events.extend(self.publish_filesystem_ready_event(roots, change_summary=change_summary, current_signature=filesystem_signature))
            session_file_events = self.publish_session_files_ready_events(trigger="fs_changed")
            if session_file_events:
                events.extend(session_file_events)
        elif self.background_can_run(BACKGROUND_ROLE_SEARCH_INDEX):
            file_index.schedule_refreshes()
        return events

    def poll_client_file_events_once(self) -> list[str]:
        started = time.perf_counter()
        files_signature = self.files_watch_signature()
        compute_ms = (time.perf_counter() - started) * 1000
        with self.client_watch_service.lock:
            initialized = self.client_watch_service.file_signature is not None
            previous = self.client_watch_service.file_signature
            self.client_watch_service.file_signature = files_signature
        if not initialized:
            return []
        return self.publish_files_changed_event(previous, files_signature, compute_ms=compute_ms)

    def poll_client_background_file_events_once(self) -> list[str]:
        started = time.perf_counter()
        files_signature = self.background_files_watch_signature()
        compute_ms = (time.perf_counter() - started) * 1000
        with self.client_watch_service.lock:
            initialized = self.client_watch_service.background_file_signature is not None
            previous = self.client_watch_service.background_file_signature
            self.client_watch_service.background_file_signature = files_signature
        if not initialized:
            return []
        return self.publish_files_changed_event(previous, files_signature, compute_ms=compute_ms)

    def publish_context_items_ready_events(self, trigger: str = "watch") -> list[str]:
        context_items, _session_files, _activity = self.client_watch_service.snapshot()
        events: list[str] = []
        for item in context_items:
            started = time.perf_counter()
            payload, status = self.context_items(item["session"], int(item["messages"]))
            event_payload = {"session": item["session"], "messages": item["messages"], "status": int(status), "data": payload}
            signature = self.client_event_payload_signature(event_payload)
            key = self.client_event_payload_signature({"session": item["session"], "messages": item["messages"]})
            with self.client_watch_service.lock:
                previous_signature = self.client_watch_service.context_item_payload_signatures.get(key)
                self.client_watch_service.context_item_payload_signatures[key] = signature
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
        _context_items, _session_files, activity_summary = self.client_watch_service.snapshot()
        if activity_summary.get("visible") is not True:
            return []
        started = time.perf_counter()
        payload = self.activity_summary_payload(
            locale=str(activity_summary.get("locale") or "en"),
            session_scope=activity_summary.get("scope"),
            hours=activity_summary.get("hours"),
        )
        signature = self.stable_client_event_payload_signature(payload)
        with self.client_watch_service.lock:
            previous_signature = self.client_watch_service.activity_summary_signature
            self.client_watch_service.activity_summary_signature = signature
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
        _context_items, session_files_requests, _activity = self.client_watch_service.snapshot()
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
            with self.client_watch_service.lock:
                previous_signature = self.client_watch_service.session_file_payload_signatures.get(key)
                self.client_watch_service.session_file_payload_signatures[key] = signature
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
        # statusd owns discovery, pane capture, classification, and encoding. The watcher only
        # observes its immutable generation so it can wake browsers to fetch the same bytes.
        response, _body = self.status_client.snapshot(self.sessions, timeout=1.0)
        if response.get("ok") is not True:
            return []
        status = HTTPStatus(int(response.get("status") or HTTPStatus.OK))
        generation = int(response.get("generation") or 0)
        signature = f"statusd:{generation}:{int(status)}"
        with self.client_watch_service.lock:
            previous = self.client_watch_service.auto_approve_signature
            self.client_watch_service.auto_approve_signature = signature
        if not previous:
            return []
        if previous == signature:
            return []
        event_payload: dict[str, Any] = {"status": int(status), "refresh": True, "signature": signature}
        event_payload["generation"] = generation
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
        with self.client_watch_service.lock:
            previous = self.client_watch_service.tmux_signal_signature
            previous_payload = copy.deepcopy(self.client_watch_service.tmux_signal_payload) if self.client_watch_service.tmux_signal_payload is not None else None
            self.client_watch_service.tmux_signal_signature = signature
            self.client_watch_service.tmux_signal_payload = copy.deepcopy(payload)
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
        if event_type in {"output", "extended-output"}:
            output_snapshot_at = time.monotonic() + TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS
            with self.client_watch_service.lock:
                record = self.client_watch_service.event_watcher_record
                next_snapshot_at = record.next_tmux_signal_poll_at
                schedule_snapshot = next_snapshot_at <= time.monotonic() or next_snapshot_at > output_snapshot_at
                if schedule_snapshot:
                    record.next_tmux_signal_poll_at = output_snapshot_at
            if schedule_snapshot:
                # Terminal bytes already travel on their own WebSocket. Coalesce the metadata
                # invalidation so a busy pane cannot launch a full tmux snapshot per output frame.
                self.tmux_signal_cache.clear()
                record.wake_event.set()
            return
        if event_type in {"pane-exited", "pane-died", "window-close", "sessions-changed"}:
            event_time = float(event.get("time") or time.time())
            with self.client_watch_service.lock:
                self.client_watch_service.tmux_signal_removal_event = {"type": event_type, "time": event_time}
        self.tmux_signal_cache.clear()
        with self.client_watch_service.lock:
            record = self.client_watch_service.event_watcher_record
            record.next_tmux_signal_poll_at = 0.0
        record.wake_event.set()

    def log_tmux_signal_event_error(self, message: str) -> None:
        self.log_event(
            None,
            "tmux_signal_event_error",
            message,
            {"diagnostic": message},
            message_key="events.message.tmuxSignalEvent.watchFailed",
        )

    def start_tmux_signal_event_watcher(self) -> bool:
        with self.client_watch_service.lock:
            if self.tmux_signal_event_watcher is not None:
                return False
            watcher = TmuxSignalEventWatcher(lambda: list(self.sessions), self.handle_tmux_signal_event, self.log_tmux_signal_event_error)
            self.tmux_signal_event_watcher = watcher
        return watcher.start()

    def stop_tmux_signal_event_watcher(self) -> None:
        with self.client_watch_service.lock:
            watcher = self.tmux_signal_event_watcher
            self.tmux_signal_event_watcher = None
        if watcher is not None:
            watcher.stop()

    def poll_watched_prs_client_event_once(self) -> list[str]:
        started = time.perf_counter()
        payload = self.watched_prs_payload()
        signature = self.client_event_payload_signature(payload)
        with self.client_watch_service.lock:
            previous = self.client_watch_service.watched_prs_signature
            self.client_watch_service.watched_prs_signature = signature
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
        with self.client_watch_service.lock:
            current = self.client_watch_service.event_watcher_record
            if current.worker is not None and current.worker.is_alive():
                return
            record = ClientEventWatcherRecord(
                next_auto_poll_at=now + self.server_auto_approve_event_poll_seconds(),
                next_attention_ack_poll_at=now + self.server_attention_ack_event_poll_seconds(),
                next_tmux_signal_poll_at=now + self.server_tmux_signal_event_poll_seconds(),
            )
            worker = threading.Thread(target=self.client_event_watch_loop, args=(record,), name="client-event-watch", daemon=True)
            record.worker = worker
            self.client_watch_service.event_watcher_record = record

        def rollback() -> None:
            owned = False
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is record and record.worker is worker:
                    record.stop_event.set()
                    record.filesystem_stop_event.set()
                    record.filesystem_reconfigure_event.set()
                    record.wake_event.set()
                    self.client_watch_service.event_watcher_record = ClientEventWatcherRecord()
                    owned = True
            if owned:
                self.stop_tmux_signal_event_watcher()

        try:
            self.start_tmux_signal_event_watcher()
        except Exception:
            rollback()
            raise
        common.start_thread_with_rollback(worker, rollback)
        try:
            self.start_native_filesystem_watcher(record)
        except RuntimeError as exc:
            # Native watching is an accelerator. Keep the established polling
            # fallback alive when a backend thread cannot start.
            self.log_event(
                None,
                "native_filesystem_watch_error",
                f"native filesystem watch failed to start: {exc}",
                {"diagnostic": str(exc)},
                message_key="events.message.clientEvent.directoryWatchFailed",
            )

    def stop_client_event_watcher(self) -> None:
        self.stop_tmux_signal_event_watcher()
        with self.client_watch_service.lock:
            record = self.client_watch_service.event_watcher_record
            record.stop_event.set()
            record.filesystem_stop_event.set()
            record.filesystem_reconfigure_event.set()
            record.wake_event.set()
            thread = record.worker
            filesystem_worker = record.filesystem_worker
            snapshot_worker = record.snapshot_worker
            record.snapshot_worker = None
        if snapshot_worker is not None:
            with self.activity_transcript_service.transcripts_payload_cache_lock:
                cache_record = self.activity_transcript_service.transcripts_payload_cache_record
                snapshot_generation = cache_record.generation if cache_record.worker is snapshot_worker else 0
            if snapshot_generation:
                self.finish_transcripts_payload_work(snapshot_generation, snapshot_worker, invalidate=True)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if filesystem_worker is not None and filesystem_worker is not threading.current_thread():
            filesystem_worker.join(timeout=2.0)
        with self.client_watch_service.lock:
            if self.client_watch_service.event_watcher_record is record:
                self.client_watch_service.event_watcher_record = ClientEventWatcherRecord()

    def stop_client_event_watcher_if_idle(self) -> bool:
        with self.client_events.lock:
            if self.client_events.subscribers:
                return False
        self.stop_client_event_watcher()
        return True

    def start_client_directory_poll(self, record: ClientEventWatcherRecord | None = None) -> bool:
        with self.client_watch_service.lock:
            current = record or self.client_watch_service.event_watcher_record
            if self.client_watch_service.event_watcher_record is not current or current.stop_event.is_set():
                return False
            worker = current.directory_poll_worker
            if worker is not None and worker.is_alive():
                return False
            worker = threading.Thread(target=self.run_client_directory_poll_once, args=(current,), daemon=True)
            current.directory_poll_worker = worker

        def rollback() -> None:
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is current and current.directory_poll_worker is worker:
                    current.directory_poll_worker = None

        common.start_thread_with_rollback(worker, rollback)
        return True

    def run_client_directory_poll_once(self, record: ClientEventWatcherRecord | None = None) -> None:
        current = record or self.client_watch_service.event_watcher_record
        worker = threading.current_thread()
        try:
            self.poll_client_events_once()
        except (OSError, RuntimeError, ValueError) as exc:
            self.log_event(
                None,
                "client_event_watch_error",
                f"client directory event watch failed: {exc}",
                {"diagnostic": str(exc)},
                message_key="events.message.clientEvent.directoryWatchFailed",
            )
        finally:
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is current and current.directory_poll_worker is worker:
                    current.directory_poll_worker = None

    def client_event_watch_loop(self, record: ClientEventWatcherRecord | None = None) -> None:
        current = record or self.client_watch_service.event_watcher_record
        worker = threading.current_thread()
        try:
            while not current.stop_event.is_set():
                try:
                    now = time.monotonic()
                    file_demand = self.client_events.has_demand("files", "transcripts", "activity")
                    status_demand = self.client_events.has_demand("status", "attention")
                    notification_demand = self.client_events.has_demand("attention")
                    if file_demand and now >= current.next_file_poll_at:
                        self.poll_client_file_events_once()
                        current.next_file_poll_at = now + self.server_event_poll_seconds()
                    if file_demand and now >= current.next_background_file_poll_at:
                        self.poll_client_background_file_events_once()
                        current.next_background_file_poll_at = now + self.server_background_file_event_poll_seconds()
                    if file_demand and not current.filesystem_healthy and now >= current.next_filesystem_retry_at:
                        self.start_native_filesystem_watcher(current)
                        current.next_filesystem_retry_at = now + NATIVE_FILESYSTEM_RETRY_SECONDS
                    if file_demand and now >= current.next_signature_poll_at:
                        current.next_signature_poll_at = now + (
                            NATIVE_FILESYSTEM_RECONCILE_SECONDS
                            if current.filesystem_healthy
                            else VISIBLE_FILESYSTEM_FALLBACK_POLL_SECONDS
                        )
                        self.start_client_directory_poll(current)
                    if status_demand and now >= current.next_auto_poll_at:
                        self.poll_auto_approve_client_event_once()
                        current.next_auto_poll_at = now + self.server_auto_approve_event_poll_seconds()
                    if status_demand and now >= current.next_attention_ack_poll_at:
                        self.poll_attention_acks_client_event_once()
                        current.next_attention_ack_poll_at = now + self.server_attention_ack_event_poll_seconds()
                    if status_demand and now >= current.next_tmux_signal_poll_at:
                        self.poll_tmux_signals_client_event_once()
                        current.next_tmux_signal_poll_at = now + self.server_tmux_signal_event_poll_seconds()
                    if (self.client_events.has_demand("core") or notification_demand) and now >= current.next_watched_pr_poll_at:
                        self.poll_watched_prs_client_event_once()
                        current.next_watched_pr_poll_at = now + self.server_watched_pr_event_poll_seconds()
                    if (self.client_events.has_demand("yoagent") or notification_demand) and now >= current.next_yoagent_job_poll_at:
                        self.yoagent_controller.poll_yoagent_jobs_once()
                        current.next_yoagent_job_poll_at = now + YOAGENT_JOB_POLL_SECONDS
                except (OSError, RuntimeError, ValueError) as exc:
                    self.log_event(
                        None,
                        "client_event_watch_error",
                        f"client event watch failed: {exc}",
                        {"diagnostic": str(exc)},
                        message_key="events.message.clientEvent.watchFailed",
                    )
                if current.wake_event.wait(self.client_event_watch_sleep_seconds(time.monotonic(), current)):
                    current.wake_event.clear()
        finally:
            with self.client_watch_service.lock:
                if self.client_watch_service.event_watcher_record is current and current.worker is worker:
                    current.worker = None

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
        repo_signatures: list[tuple[str, tuple[Any, ...]]] = []
        repo_roots = {
            repo
            for info in infos.values()
            for repo in session_files.session_candidate_repo_roots(info)
        }
        for repo_text in sorted(repo_roots):
            override = (repo_refs or {}).get(repo_text) or {}
            repo_from = str(override.get("from") or "").strip() or from_ref
            repo_to = str(override.get("to") or "").strip() or to_ref
            repo = Path(repo_text)
            repo_signatures.append((repo_text, self.shared_git_identity(repo, repo_from, repo_to)[0]))
        return (
            kind,
            SESSION_FILES_CACHE_KEY_VERSION,
            session or "",
            session_files.bounded_session_files_hours(hours),
            str(from_ref or ""),
            str(to_ref or ""),
            repo_refs_cache_signature(repo_refs),
            tuple((name, session_info_cache_signature(info)) for name, info in sorted(infos.items())),
            tuple(repo_signatures),
        )

    def session_files_refresh_request_payload(
        self,
        cache_key: tuple[Any, ...],
        session: str | None,
        hours: float,
        from_ref: str | None,
        to_ref: str | None,
        repo_refs: dict[str, dict[str, str]] | None,
    ) -> dict[str, Any]:
        return {
            "session": session or "",
            "hours": session_files.bounded_session_files_hours(hours),
            "from_ref": str(from_ref or ""),
            "to_ref": str(to_ref or ""),
            "repo_refs": repo_refs or {},
            "cache_key": repr(cache_key),
            "cache_key_data": cache_key,
        }

    def requested_session_files_cache_key(
        self,
        payload: dict[str, Any],
        fallback: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        def freeze(value: Any) -> Any:
            if isinstance(value, (list, tuple)):
                return tuple(freeze(item) for item in value)
            return value

        requested = freeze(payload.get("cache_key_data"))
        if not isinstance(requested, tuple) or len(requested) != len(fallback):
            return fallback
        # The owner may observe newer tmux/transcript metadata or repository state than the follower,
        # so the final info/repo signatures may differ. All request-controlled dimensions must match
        # before the owner writes its current result under the follower's key.
        if requested[:-2] != fallback[:-2]:
            return fallback
        return requested

    # ---- Repository-state record (DOIT.optimize-backends) ------------------
    # One dirty generation per canonical Git root, bumped by the native
    # filesystem watcher (worktree AND .git metadata events). A cached identity
    # is reusable only while the watcher is healthy, the repo lies under a
    # watched root, the generation is unchanged, and the entry is younger than
    # the safety-reconciliation bound (missed/coalesced events self-heal).
    SESSION_FILES_GIT_IDENTITY_SAFETY_SECONDS = 60.0

    def repo_dirty_generation(self, repo_text: str) -> int:
        with self.session_files_service.cache_lock:
            return self.session_files_service.repo_dirty_generations.setdefault(repo_text, 0)

    def mark_repo_state_dirty(self, changed_paths: list[Path]) -> None:
        with self.session_files_service.cache_lock:
            generations = self.session_files_service.repo_dirty_generations
            for repo_text in generations:
                repo_path = Path(repo_text)
                if any(
                    path == repo_path or filesystem._path_is_within(path, repo_path)
                    for path in changed_paths
                ):
                    generations[repo_text] += 1

    def store_git_identity(self, identity_key: tuple[Any, ...], dirty_generation: int, identity: tuple[Any, ...]) -> None:
        with self.session_files_service.cache_lock:
            self.session_files_service.repo_identity_cache[identity_key] = (dirty_generation, time.monotonic(), identity)

    def reusable_git_identity(self, identity_key: tuple[Any, ...], repo: Path) -> tuple[Any, ...] | None:
        record = self.client_watch_service.event_watcher_record
        if not record.filesystem_healthy:
            return None
        resolved_repo = Path(str(repo)).expanduser().resolve(strict=False)
        if not any(
            resolved_repo == Path(root) or filesystem._path_is_within(resolved_repo, Path(root))
            for root in record.filesystem_roots
        ):
            return None
        with self.session_files_service.cache_lock:
            entry = self.session_files_service.repo_identity_cache.get(identity_key)
            if entry is None:
                return None
            generation_at_compute, computed_at, identity = entry
            if generation_at_compute != self.session_files_service.repo_dirty_generations.get(identity_key[0], 0):
                return None
        if time.monotonic() - computed_at > self.SESSION_FILES_GIT_IDENTITY_SAFETY_SECONDS:
            return None
        return identity

    def session_files_disk_cache_path(self, key: tuple[Any, ...]) -> tuple[Path, str]:
        # Stable logical view identity only (kind, version, session, hours, refs,
        # per-repo ref overrides): the volatile info/repo signatures (key[-2:])
        # are a replaceable source generation stored INSIDE the record, so agent
        # status or transcript appends REPLACE one durable file per view instead
        # of minting a new filename per generation.
        key_text = self.client_event_payload_signature(key[:-2])
        signature = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        return SESSION_FILES_CACHE_DIR / f"{signature}.json", signature

    def session_files_source_generation(self, key: tuple[Any, ...]) -> str:
        """The replaceable half of the cache identity: info + repo signatures."""
        return hashlib.sha256(self.client_event_payload_signature(key[-2:]).encode("utf-8")).hexdigest()

    def session_files_disk_manifest_path(self, signature: str) -> Path:
        return SESSION_FILES_CACHE_DIR / f"{signature}.manifest.json"

    def session_files_disk_cache_index_path(self) -> Path:
        return SESSION_FILES_CACHE_DIR / SESSION_FILES_DISK_CACHE_INDEX_FILENAME

    def empty_session_files_disk_cache_index(self) -> dict[str, Any]:
        return {"version": SESSION_FILES_DISK_CACHE_INDEX_VERSION, "entries": {}, "recovery_cursor": ""}

    def session_files_disk_cache_index_journal_path(self) -> Path:
        return self.session_files_disk_cache_index_path().with_name(
            self.session_files_disk_cache_index_path().name + ".journal"
        )

    def read_session_files_disk_cache_index_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.session_files_disk_cache_index_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            payload = None
        entries = payload.get("entries") if isinstance(payload, dict) else None
        index = {
            "version": SESSION_FILES_DISK_CACHE_INDEX_VERSION,
            "entries": (
                {str(signature): value for signature, value in entries.items() if isinstance(value, dict)}
                if isinstance(entries, dict) else {}
            ),
            "recovery_cursor": str(payload.get("recovery_cursor") or "") if isinstance(payload, dict) else "",
        }
        # Per-write updates are appended to a journal instead of rewriting the whole
        # base index (which was O(historical entries) bytes per durable write); the
        # base + journal merge here is the one read-side owner, and every full base
        # rewrite folds the journal in and truncates it.
        try:
            journal_lines = self.session_files_disk_cache_index_journal_path().read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            journal_lines = []
        for line in journal_lines:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(record, dict) and isinstance(record.get("signature"), str):
                index["entries"][record["signature"]] = {
                    "size": max(0, int(record.get("size") or 0)),
                    "mtime": max(0.0, float(record.get("mtime") or 0.0)),
                }
        return index

    def write_session_files_disk_cache_index_unlocked(self, index: dict[str, Any]) -> None:
        atomic_write_text(
            self.session_files_disk_cache_index_path(),
            json.dumps(index, sort_keys=True, separators=(",", ":")),
            mode=0o600,
        )
        # The base now contains everything the journal recorded (callers read the
        # merged view before rewriting), so the journal restarts empty.
        try:
            self.session_files_disk_cache_index_journal_path().unlink(missing_ok=True)
        except OSError:
            pass

    def update_session_files_disk_cache_index(self, signature: str, *, size: int, mtime: float) -> None:
        index_path = self.session_files_disk_cache_index_path()
        try:
            with file_lock(index_path, dir_mode=0o700):
                journal_path = self.session_files_disk_cache_index_journal_path()
                line = json.dumps(
                    {"signature": str(signature), "size": max(0, int(size)), "mtime": max(0.0, float(mtime))},
                    sort_keys=True, separators=(",", ":"),
                ) + "\n"
                with open(journal_path, "a", encoding="utf-8") as journal:
                    journal.write(line)
                # Bound the journal between prunes: fold it into the base once it
                # grows past a fixed size, so reads stay cheap and the file cannot
                # grow without limit. The fold is the ONLY O(entries) write, and it
                # runs once per ~512 KiB of appends instead of once per write.
                try:
                    journal_bytes = journal_path.stat().st_size
                except OSError:
                    journal_bytes = 0
                if journal_bytes > 512 * 1024:
                    self.write_session_files_disk_cache_index_unlocked(
                        self.read_session_files_disk_cache_index_unlocked()
                    )
        except OSError as exc:
            logger.debug("failed to update session-files cache index: %s", exc)

    def recover_session_files_disk_cache_index_batch(self, index: dict[str, Any]) -> int:
        """Adopt a bounded number of old cache files when the shared index is absent/corrupt."""
        adopted = 0
        try:
            paths = SESSION_FILES_CACHE_DIR.iterdir()
        except OSError:
            return 0
        for path in paths:
            name = path.name
            if not name.endswith(".json") or name.endswith(".manifest.json") or name == SESSION_FILES_DISK_CACHE_INDEX_FILENAME:
                continue
            signature = path.stem
            if signature in index["entries"]:
                continue
            try:
                payload_stat = path.stat()
            except OSError:
                continue
            manifest_path = self.session_files_disk_manifest_path(signature)
            size = int(payload_stat.st_size)
            mtime = float(payload_stat.st_mtime)
            try:
                manifest_stat = manifest_path.stat()
                size += int(manifest_stat.st_size)
                mtime = max(mtime, float(manifest_stat.st_mtime))
            except OSError:
                pass
            index["entries"].setdefault(signature, {"size": size, "mtime": mtime})
            adopted += 1
            if adopted >= SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE:
                break
        # Filesystem directory order is not stable, so use the indexed signatures rather than a
        # lexical cursor.  Each pass stats only newly discovered entries and stops at the batch
        # limit; a later maintenance pass adopts the next missing entries safely.
        index["recovery_cursor"] = ""
        return adopted

    def session_files_disk_cache_entries(self) -> list[dict[str, Any]]:
        try:
            paths = sorted(SESSION_FILES_CACHE_DIR.glob("*.json"))
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for path in paths:
            if path.name.endswith(".manifest.json") or path.name == SESSION_FILES_DISK_CACHE_INDEX_FILENAME:
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
        index_path = self.session_files_disk_cache_index_path()
        try:
            with file_lock(index_path, dir_mode=0o700):
                index = self.read_session_files_disk_cache_index_unlocked()
                recovered_entries = self.recover_session_files_disk_cache_index_batch(index)
                indexed_entries = index["entries"]
                entries = [
                    {
                        "path": SESSION_FILES_CACHE_DIR / f"{signature}.json",
                        "manifest_path": self.session_files_disk_manifest_path(signature),
                        "signature": signature,
                        "size": max(0, int(self.float_value(metadata.get("size"), 0.0))),
                        "mtime": max(0.0, self.float_value(metadata.get("mtime"), 0.0)),
                    }
                    for signature, metadata in indexed_entries.items()
                ]
                self.write_session_files_disk_cache_index_unlocked(index)
        except OSError:
            # Keep the old recovery path for a transient index lock/filesystem failure.
            entries = self.session_files_disk_cache_entries()
            recovered_entries = 0
        total_indexed_bytes = sum(max(0, int(self.float_value(entry.get("size"), 0.0))) for entry in entries)
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
        for entry in to_remove[:SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE]:
            path = entry.get("path")
            if isinstance(path, Path):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
            files, bytes_removed = self.remove_session_files_disk_cache_entry(entry)
            removed_files += files
            removed_bytes += bytes_removed
        if seen_paths:
            try:
                with file_lock(index_path, dir_mode=0o700):
                    index = self.read_session_files_disk_cache_index_unlocked()
                    for entry in to_remove[:SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE]:
                        index["entries"].pop(str(entry.get("signature") or ""), None)
                    self.write_session_files_disk_cache_index_unlocked(index)
            except OSError:
                pass
        return {
            "entries": len(entries),
            "recovered_entries": recovered_entries,
            "removed_entries": len(seen_paths),
            "removed_files": removed_files,
            "removed_bytes": removed_bytes,
            "kept_bytes": max(0, total_indexed_bytes - removed_bytes),
            "max_age_seconds": max_age,
            "max_bytes": byte_cap,
        }

    def run_session_files_disk_cache_prune(self, record: SessionFilesDiskPruneRecord | None = None) -> None:
        active_record = record or self.session_files_service.disk_prune_record
        try:
            result = self.prune_session_files_disk_cache()
        except (OSError, RuntimeError, ValueError) as exc:
            result = {"error": str(exc)}
            logger.warning("session-files disk cache prune failed: %s", exc)
        with self.session_files_service.disk_prune_lock:
            if self.session_files_service.disk_prune_record is active_record:
                active_record.last_result = result
                active_record.running = False
                active_record.worker = None
        if result.get("removed_entries"):
            self.log_event(
                None,
                "session_files_cache_pruned",
                "Session-files disk cache pruned",
                result,
                message_key="events.message.sessionFiles.cachePruned",
            )

    def request_session_files_disk_cache_prune(self, reason: str = "") -> bool:
        now = time.monotonic()
        with self.session_files_service.disk_prune_lock:
            record = self.session_files_service.disk_prune_record
            if record.running or now < record.next_at:
                return False
            record.running = True
            record.next_at = now + SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS
            worker = threading.Thread(target=lambda: self.run_session_files_disk_cache_prune(record), name="session-files-cache-prune", daemon=True)
            record.worker = worker

        def rollback() -> None:
            with self.session_files_service.disk_prune_lock:
                if self.session_files_service.disk_prune_record is record and record.worker is worker:
                    record.worker = None
                    record.running = False
                    record.next_at = 0.0

        common.start_thread_with_rollback(worker, rollback)
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
        with self.session_files_service.cache_lock:
            self.cache_set_limited(
                self.session_files_service.cache,
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
        source_generation = self.session_files_source_generation(key)
        stored_generation = str(record.get("source_generation") or "")
        if stored_generation != source_generation:
            # Same logical view, older source generation: usable only as a
            # stale last-known-good. Never memory-cache it under the requested
            # key — a strict fresh lookup must not see it as current.
            if not allow_stale:
                return None
            payload_stale = record.get("payload")
            try:
                status_stale = HTTPStatus(int(record.get("status", int(HTTPStatus.OK))))
                stored_at_stale = float(record.get("stored_at", 0.0))
            except (TypeError, ValueError):
                return None
            age_stale = max(0.0, time.time() - stored_at_stale)
            return copy.deepcopy(payload_stale), status_stale, False, age_stale
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
        source_generation: str = "",
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
            payload_changed = (
                existing_payload_signature != payload_signature
                or existing_status != int(status)
                or str(existing.get("source_generation") or "") != source_generation
            )
        stored_at = time.time()
        record = {
            "version": SESSION_FILES_CACHE_VERSION,
            "signature": signature,
            "source_generation": source_generation,
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
        try:
            payload_size = path.stat().st_size
        except OSError:
            payload_size = 0
        try:
            manifest_stat = self.session_files_disk_manifest_path(signature).stat()
            payload_size += manifest_stat.st_size
            mtime = max(stored_at, float(manifest_stat.st_mtime))
        except OSError:
            mtime = stored_at
        self.update_session_files_disk_cache_index(signature, size=payload_size, mtime=mtime)
        # Accounting (DOIT.optimize-backends): every durable generation write is a
        # counted event with its payload+manifest bytes and the rewritten index
        # size, so the bounded-storage rework can be justified from measurements.
        try:
            index_bytes = self.session_files_disk_cache_index_path().stat().st_size
        except OSError:
            index_bytes = 0
        self.record_session_files_phase(
            "durable-cache-write", 0.0,
            {"payload_and_manifest_bytes": int(payload_size), "index_bytes_rewritten": int(index_bytes)},
        )
        self.request_session_files_disk_cache_prune("write")

    def write_session_files_disk_cache(self, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
        path, signature = self.session_files_disk_cache_path(key)
        try:
            with file_lock(path, dir_mode=0o700):
                self.write_session_files_disk_cache_unlocked(path, signature, payload, status, self.session_files_source_generation(key))
        except OSError as exc:
            logger.warning("failed to write session-files cache %s: %s", path, exc)

    def record_session_files_phase(self, phase: str, compute_ms: float, details: dict[str, Any]) -> None:
        self.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            f"phase:{str(phase or 'unknown')[:80]}",
            trigger="payload",
            compute_ms=compute_ms,
            cache_key={"kind": "session-files-phase"},
            cache_status="computed",
            details=details,
        )

    def shared_git_identity(self, repo: Path, from_ref: str | None, to_ref: str | None) -> tuple[tuple[Any, ...], str]:
        """One identity owner for BOTH the cache-key path and the snapshot path.

        Order of preference: watcher-cached (repository-state record unchanged
        -> ZERO Git commands), coalesced (another caller is computing it right
        now), computed. The future is removed once it completes, so sequential
        calls still recompute when the record is dirty and a file change is
        visible to the very next request — nothing is delayed or hidden.
        """
        identity_key = (str(repo), from_ref or "", to_ref or "")
        cached_identity = self.reusable_git_identity(identity_key, repo)
        if cached_identity is not None:
            return cached_identity, "watcher-cached"
        with self.session_files_service.cache_lock:
            identity_future = self.session_files_service.git_identity_futures.get(identity_key)
            identity_owner = identity_future is None
            if identity_owner:
                identity_future = Future()
                self.session_files_service.git_identity_futures[identity_key] = identity_future
        if not identity_owner:
            return identity_future.result(), "coalesced"
        try:
            dirty_generation_at_start = self.repo_dirty_generation(identity_key[0])
            snapshot_identity = session_files.git_snapshot_identity(repo, from_ref, to_ref)
            self.store_git_identity(identity_key, dirty_generation_at_start, snapshot_identity)
            identity_future.set_result(snapshot_identity)
        except BaseException as error:
            identity_future.set_exception(error)
            raise
        finally:
            with self.session_files_service.cache_lock:
                self.session_files_service.git_identity_futures.pop(identity_key, None)
        return snapshot_identity, "computed"

    def shared_session_files_git_snapshot(
        self,
        repo: Path,
        from_ref: str | None,
        to_ref: str | None,
        *,
        identity: tuple[Any, ...] | None = None,
    ) -> dict[str, Any]:
        signature_started = time.perf_counter()
        if identity is not None:
            snapshot_identity, identity_status = identity, "provided"
        else:
            snapshot_identity, identity_status = self.shared_git_identity(repo, from_ref, to_ref)
        self.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            "phase:git-signature",
            trigger="payload",
            compute_ms=(time.perf_counter() - signature_started) * 1000,
            cache_key={"kind": "git-snapshot"},
            cache_status=identity_status,
            cache_hit=identity_status != "computed",
            details={"repo": str(repo)},
        )
        with self.session_files_service.cache_lock:
            record = self.session_files_service.git_snapshot_records.get(snapshot_identity)
            if record is not None and record.snapshot is not None:
                self.record_performance_sample(
                    BACKGROUND_ROLE_SESSION_FILES,
                    "phase:git-snapshot",
                    trigger="payload",
                    compute_ms=0,
                    cache_key={"kind": "git-snapshot"},
                    cache_status="hit:fresh",
                    cache_hit=True,
                    cache_fresh=True,
                    details={"repo": str(repo)},
                )
                return copy.deepcopy(record.snapshot)
            if record is None:
                record = SessionFilesGitSnapshotRecord()
                self.session_files_service.git_snapshot_records[snapshot_identity] = record
                owner = True
            else:
                owner = False
        if not owner:
            final_identity, snapshot = record.future.result()
            self.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "phase:git-snapshot",
                trigger="payload",
                compute_ms=0,
                cache_key={"kind": "git-snapshot"},
                cache_status="coalesced",
                cache_hit=True,
                cache_fresh=final_identity == snapshot_identity,
                details={"repo": str(repo)},
            )
            if final_identity != snapshot_identity:
                return self.shared_session_files_git_snapshot(repo, from_ref, to_ref)
            return copy.deepcopy(snapshot)
        started = time.perf_counter()
        try:
            snapshot = session_files.build_git_snapshot(repo, from_ref, to_ref)
            final_identity = session_files.git_snapshot_identity(repo, from_ref, to_ref)
            compute_ms = (time.perf_counter() - started) * 1000
            self.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "phase:git-snapshot",
                trigger="payload",
                compute_ms=compute_ms,
                cache_key={"kind": "git-snapshot"},
                cache_status="miss:computed",
                cache_hit=False,
                cache_fresh=final_identity == snapshot_identity,
                details={"repo": str(repo)},
            )
            record.future.set_result((final_identity, copy.deepcopy(snapshot)))
            with self.session_files_service.cache_lock:
                if final_identity == snapshot_identity:
                    record.snapshot = copy.deepcopy(snapshot)
                    while len(self.session_files_service.git_snapshot_records) > SESSION_FILES_GIT_SNAPSHOT_MAX_ITEMS:
                        oldest_key = next(iter(self.session_files_service.git_snapshot_records))
                        if oldest_key == snapshot_identity and len(self.session_files_service.git_snapshot_records) > 1:
                            oldest_key = next(key for key in self.session_files_service.git_snapshot_records if key != snapshot_identity)
                        self.session_files_service.git_snapshot_records.pop(oldest_key, None)
                elif self.session_files_service.git_snapshot_records.get(snapshot_identity) is record:
                    self.session_files_service.git_snapshot_records.pop(snapshot_identity, None)
            if final_identity != snapshot_identity:
                return self.shared_session_files_git_snapshot(repo, from_ref, to_ref)
            return copy.deepcopy(snapshot)
        except Exception as exc:
            if not record.future.done():
                record.future.set_exception(exc)
            with self.session_files_service.cache_lock:
                if self.session_files_service.git_snapshot_records.get(snapshot_identity) is record:
                    self.session_files_service.git_snapshot_records.pop(snapshot_identity, None)
            raise

    def complete_session_files_work(
        self,
        key: tuple[Any, ...],
        record: SessionFilesWorkRecord,
        result: tuple[SessionFilesPayload, HTTPStatus, bool, float] | None = None,
        error: Exception | None = None,
    ) -> None:
        if error is not None and not record.future.done():
            record.future.set_exception(error)
        elif result is not None and not record.future.done():
            record.future.set_result((copy.deepcopy(result[0]), result[1], result[2], result[3]))
        self.session_files_service.finish_work(key, record)

    def compute_session_files_cache_entry(
        self,
        key: tuple[Any, ...],
        compute: Callable[[], tuple[SessionFilesPayload, HTTPStatus]],
        *,
        reserved: bool = False,
    ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float]:
        path, signature = self.session_files_disk_cache_path(key)
        work_record, owner = self.session_files_service.claim_work(
            key,
            threading.get_ident(),
            reserved=reserved,
            stable_signature=signature,
        )
        if not owner:
            payload, status, cache_hit, age_seconds = work_record.future.result()
            self.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "cache-entry",
                trigger="single-flight",
                compute_ms=0,
                payload=payload,
                cache_key=key,
                cache_status="coalesced",
                cache_hit=True,
                cache_fresh=True,
            )
            return copy.deepcopy(payload), status, cache_hit, age_seconds
        started = time.perf_counter()
        compute_attempted = False
        compute_slot_acquired = False
        computed_result: tuple[SessionFilesPayload, HTTPStatus] | None = None
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
                    result = (payload, status, True, age_seconds)
                    self.complete_session_files_work(key, work_record, result=result)
                    return result
                # Only a true cache miss enters the owner-wide queue.  Hits and
                # followers remain cheap, while unrelated HTTP handlers never
                # contend for these transcript/Git slots.
                queue_started = time.perf_counter()
                self.session_files_service.acquire_compute_slot(self.session_files_max_workers())
                compute_slot_acquired = True
                self.record_session_files_phase(
                    "cold-rebuild-queue",
                    (time.perf_counter() - queue_started) * 1000,
                    {"cache_key_kind": self.performance_cache_key_kind(key)},
                )
                compute_attempted = True
                payload, status = compute()
                computed_result = (payload, status)
                serialization_started = time.perf_counter()
                if self.session_files_service.stable_generation_is_current(work_record):
                    self.set_session_files_memory_cache(key, payload, status)
                    self.write_session_files_disk_cache_unlocked(path, signature, payload, status, self.session_files_source_generation(key))
                self.record_session_files_phase(
                    "cache-serialization",
                    (time.perf_counter() - serialization_started) * 1000,
                    {
                        "cache_key_kind": self.performance_cache_key_kind(key),
                        "payload_bytes": self.performance_payload_bytes(payload),
                        # Cumulative work counters (git spawns per verb, catalog
                        # traversal, append bytes, untracked stat/read work) so
                        # per-build deltas are derivable from consecutive samples.
                        "runtime_counters": session_files.session_files_runtime_counters(),
                    },
                )
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
                result = (copy.deepcopy(payload), status, False, 0.0)
                self.complete_session_files_work(key, work_record, result=result)
                return result
        except OSError as exc:
            logger.warning("failed to lock session-files cache %s: %s", path, exc)
            if compute_attempted:
                if computed_result is None:
                    self.complete_session_files_work(key, work_record, error=exc)
                    raise
                payload, status = computed_result
                result = (copy.deepcopy(payload), status, False, 0.0)
                self.complete_session_files_work(key, work_record, result=result)
                return result
            try:
                queue_started = time.perf_counter()
                self.session_files_service.acquire_compute_slot(self.session_files_max_workers())
                compute_slot_acquired = True
                self.record_session_files_phase(
                    "cold-rebuild-queue",
                    (time.perf_counter() - queue_started) * 1000,
                    {"cache_key_kind": self.performance_cache_key_kind(key), "lock_fallback": True},
                )
                compute_attempted = True
                payload, status = compute()
            except Exception as compute_exc:
                self.complete_session_files_work(key, work_record, error=compute_exc)
                raise
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
            result = (copy.deepcopy(payload), status, False, 0.0)
            self.complete_session_files_work(key, work_record, result=result)
            return result
        except Exception as exc:
            self.complete_session_files_work(key, work_record, error=exc)
            raise
        finally:
            if compute_slot_acquired:
                self.session_files_service.release_compute_slot()

    def get_session_files_cache(
        self,
        key: tuple[Any, ...],
        max_age_seconds: float | None = None,
        allow_stale: bool = False,
    ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
        started = time.perf_counter()
        now = time.monotonic()
        stale_cached: tuple[SessionFilesPayload, HTTPStatus, bool, float] | None = None
        with self.session_files_service.cache_lock:
            cached = self.session_files_service.cache.get(key)
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
        with self.session_files_service.cache_lock:
            self.session_files_service.cache.clear()
            self.session_files_service.git_snapshot_records.clear()

    def session_files_git_identity_for_cache_key(self, cache_key: tuple[Any, ...] | None, repo: Path) -> tuple[Any, ...] | None:
        if not cache_key or not isinstance(cache_key[-1], tuple):
            return None
        canonical_repo = str(repo.expanduser().resolve(strict=False))
        for item in cache_key[-1]:
            if not isinstance(item, tuple) or len(item) != 2 or str(item[0]) != canonical_repo:
                continue
            return item[1] if isinstance(item[1], tuple) else None
        return None

    def session_files_git_snapshot_provider(self, cache_key: tuple[Any, ...] | None) -> Callable[[Path, str | None, str | None], dict[str, Any]]:
        def provider(repo: Path, repo_from: str | None, repo_to: str | None) -> dict[str, Any]:
            return self.shared_session_files_git_snapshot(repo, repo_from, repo_to, identity=self.session_files_git_identity_for_cache_key(cache_key, repo))
        return provider

    def compute_session_files_payload_for_info(
        self,
        info: SessionInfo,
        hours: float,
        from_ref: str | None,
        to_ref: str | None,
        repo_refs: dict[str, dict[str, str]] | None,
        cache_key: tuple[Any, ...] | None = None,
    ) -> SessionFilesPayload:
        return session_files.session_files_payload_for_info(
            info,
            hours=hours,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            git_snapshot_provider=self.session_files_git_snapshot_provider(cache_key),
            phase_recorder=self.record_session_files_phase,
        )

    def compute_session_files_payload_for_infos(
        self,
        session: str | None,
        infos: dict[str, SessionInfo],
        hours: float,
        from_ref: str | None,
        to_ref: str | None,
        repo_refs: dict[str, dict[str, str]] | None,
        cache_key: tuple[Any, ...] | None = None,
    ) -> tuple[SessionFilesPayload, HTTPStatus]:
        return session_files.session_files_payload(
            session,
            infos,
            hours,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            include_cross_session_attribution=not bool(session),
            git_snapshot_provider=self.session_files_git_snapshot_provider(cache_key),
            phase_recorder=self.record_session_files_phase,
        )

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
            message_key="events.message.backgroundRefresh.started",
            message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
        )
        try:
            self.compute_session_files_cache_entry(
                cache_key,
                lambda: self.compute_session_files_payload_for_infos(session, infos, hours, from_ref, to_ref, repo_refs, cache_key),
                reserved=True,
            )
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            self.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh finished",
                done_details,
                message_key="events.message.backgroundRefresh.finished",
                message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
            )
            self.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {**refresh_details, "compute_ms": compute_ms})
        except Exception as exc:
            logger.warning("session-files payload refresh failed for %s: %s", cache_key, exc)
            raise

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
            message_key="events.message.backgroundRefresh.started",
            message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
        )
        try:
            self.compute_session_files_cache_entry(
                cache_key,
                lambda: (self.compute_session_files_payload_for_info(info, hours, from_ref, to_ref, repo_refs, cache_key), HTTPStatus.OK),
                reserved=True,
            )
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            self.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh finished",
                done_details,
                message_key="events.message.backgroundRefresh.finished",
                message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
            )
            self.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {**refresh_details, "compute_ms": compute_ms})
        except Exception as exc:
            logger.warning("session-files info refresh failed for %s: %s", cache_key, exc)
            raise

    def start_session_files_cache_refresh(self, cache_key: tuple[Any, ...], target: Any, *args: Any) -> bool:
        if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            if target == self.refresh_session_files_payload_cache and len(args) >= 6:
                session, _infos, hours, from_ref, to_ref, repo_refs = args[:6]
                request_payload = self.session_files_refresh_request_payload(cache_key, session, hours, from_ref, to_ref, repo_refs)
            else:
                request_payload = {"cache_key": repr(cache_key), "cache_key_data": cache_key}
            self.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, request_payload)
            return False
        _path, stable_signature = self.session_files_disk_cache_path(cache_key)
        record = self.session_files_service.reserve_work(cache_key, stable_signature)
        if record is None:
            return False
        worker = threading.Thread(target=target, args=(cache_key, *args), daemon=True)
        try:
            worker.start()
        except RuntimeError as exc:
            self.complete_session_files_work(cache_key, record, error=exc)
            raise
        return True

    def start_requested_session_files_cache_refresh(self, payload: dict[str, Any]) -> bool:
        session = str(payload.get("session") or "").strip()
        scope = [session] if session else list(self.sessions)
        infos, _errors = discover_sessions(scope)
        if session and session not in infos:
            return False
        hours = session_files.bounded_session_files_hours(self.float_value(payload.get("hours"), 24.0))
        from_ref = str(payload.get("from_ref") or "").strip() or None
        to_ref = str(payload.get("to_ref") or "").strip() or None
        raw_repo_refs = payload.get("repo_refs")
        repo_refs = raw_repo_refs if isinstance(raw_repo_refs, dict) else {}
        fallback_key = self.session_files_cache_key("payload", infos, session or None, hours, from_ref, to_ref, repo_refs)
        cache_key = self.requested_session_files_cache_key(payload, fallback_key)
        return self.start_session_files_cache_refresh(
            cache_key,
            self.refresh_session_files_payload_cache,
            session or None,
            infos,
            hours,
            from_ref,
            to_ref,
            repo_refs,
        )

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
                    refresh_result = self.request_background_refresh(
                        BACKGROUND_ROLE_SESSION_FILES,
                        self.session_files_refresh_request_payload(key, info.session, hours, from_ref, to_ref, repo_refs),
                    )
                    self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                    if self.background_refresh_should_fallback(refresh_result):
                        payload, _status, _hit, _age = self.compute_session_files_cache_entry(
                            key,
                            lambda: (self.compute_session_files_payload_for_info(info, hours, from_ref, to_ref, repo_refs, key), HTTPStatus.OK),
                        )
            return payload
        if not self.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            refresh_result = self.request_background_refresh(
                BACKGROUND_ROLE_SESSION_FILES,
                self.session_files_refresh_request_payload(key, info.session, hours, from_ref, to_ref, repo_refs),
            )
            self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
            if self.background_refresh_should_fallback(refresh_result):
                payload, _status, _hit, _age = self.compute_session_files_cache_entry(
                    key,
                    lambda: (self.compute_session_files_payload_for_info(info, hours, from_ref, to_ref, repo_refs, key), HTTPStatus.OK),
                )
                return copy.deepcopy(payload)
            return {"files": [], "repos": [], "errors": [], "refreshing_elsewhere": True}
        payload, _status, _hit, _age = self.compute_session_files_cache_entry(
            key,
            lambda: (self.compute_session_files_payload_for_info(info, hours, from_ref, to_ref, repo_refs, key), HTTPStatus.OK),
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
        for session, info in infos.items():
            payloads[session] = self.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
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
        extra_errors: list[str | dict[str, Any]] | None = None,
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
                    refresh_result = self.request_background_refresh(
                        BACKGROUND_ROLE_SESSION_FILES,
                        self.session_files_refresh_request_payload(cache_key, session, hours, from_ref, to_ref, repo_refs),
                    )
                    self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                    if self.background_refresh_should_fallback(refresh_result):
                        payload, status, cache_hit, age_seconds = self.compute_session_files_cache_entry(
                            cache_key,
                            lambda: self.compute_session_files_payload_for_infos(session, infos, hours, from_ref, to_ref, repo_refs, cache_key),
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
                refresh_result = self.request_background_refresh(
                    BACKGROUND_ROLE_SESSION_FILES,
                    self.session_files_refresh_request_payload(cache_key, session, hours, from_ref, to_ref, repo_refs),
                )
                self.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                if self.background_refresh_should_fallback(refresh_result):
                    payload, status, cache_hit, age_seconds = self.compute_session_files_cache_entry(
                        cache_key,
                        lambda: self.compute_session_files_payload_for_infos(session, infos, hours, from_ref, to_ref, repo_refs, cache_key),
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
                    lambda: self.compute_session_files_payload_for_infos(session, infos, hours, from_ref, to_ref, repo_refs, cache_key),
                )
                cache_meta = {
                    "hit": cache_hit,
                    "stale": False,
                    "age_seconds": round(age_seconds, 3),
                    "refresh_seconds": max_age,
                    "refreshing": False,
                }
        payload = copy.deepcopy(payload)
        structured_extra_errors = [
            value if isinstance(value, dict) else message_descriptor("diff.warning.discovery", value, {"error": value})
            for value in (extra_errors or [])
        ]
        payload["errors"] = [*structured_extra_errors, *payload.get("errors", [])]
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
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if record.stored_at is None or record.payload is None:
                return None
            age_seconds = max(0.0, now - record.stored_at)
            fresh = age_seconds <= max_age_seconds
            if not fresh and not allow_stale:
                return None
            return copy.deepcopy(record.payload), fresh, age_seconds

    def begin_transcripts_payload_work(self, worker: object | None, *, replace: bool = False) -> int:
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if record.worker is not None and not replace:
                return 0
            record.generation += 1
            record.worker = worker
            return record.generation

    def commit_transcripts_payload_cache(self, payload: dict[str, Any], generation: int) -> bool:
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if generation <= 0 or record.generation != generation:
                return False
            record.stored_at = time.monotonic()
            record.payload = copy.deepcopy(payload)
            return True

    def finish_transcripts_payload_work(
        self,
        generation: int,
        worker: object | None,
        *,
        invalidate: bool = False,
    ) -> bool:
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if record.generation != generation or record.worker is not worker:
                return False
            if invalidate:
                record.generation += 1
            record.worker = None
            return True

    def set_transcripts_payload_cache(self, payload: dict[str, Any]) -> None:
        generation = self.begin_transcripts_payload_work(None, replace=True)
        self.commit_transcripts_payload_cache(payload, generation)

    def start_transcripts_payload_refresh(self, publish: bool = False, defer: bool = False) -> bool:
        generation = 0
        worker: object | None = None
        def run() -> None:
            self.refresh_transcripts_payload_cache(publish, generation=generation, worker=worker)

        if defer:
            worker = threading.Timer(0.05, run)
            worker.daemon = True
        else:
            worker = threading.Thread(target=run, daemon=True)
        generation = self.begin_transcripts_payload_work(worker)
        if generation <= 0:
            return False
        try:
            worker.start()
        except RuntimeError:
            self.finish_transcripts_payload_work(generation, worker, invalidate=True)
            raise
        return True

    def refresh_transcripts_payload_cache(
        self,
        publish: bool = False,
        *,
        generation: int | None = None,
        worker: object | None = None,
    ) -> None:
        current_worker = worker if worker is not None else threading.current_thread()
        if generation is None:
            generation = self.begin_transcripts_payload_work(current_worker, replace=True)
        try:
            payload = self.build_transcripts_payload()
            if not self.commit_transcripts_payload_cache(payload, generation):
                return
            if publish:
                payload_signature = self.transcripts_payload_event_signature(payload)
                with self.client_watch_service.lock:
                    self.client_watch_service.transcripts_payload_signature = payload_signature
                self.publish_client_event(
                    "transcripts_changed",
                    {"data": payload},
                    trigger="transcripts_refresh",
                    cache="ready",
                )
        finally:
            self.finish_transcripts_payload_work(generation, current_worker)

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
                    message_key="info.watched.truncated",
                    message_params={"count": truncated},
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
        work: dict[str, Any],
        files_payload: dict[str, Any],
        summary: dict[str, Any],
        recent_events: list[dict[str, Any]] | None = None,
        locale: str = "en",
    ) -> dict[str, Any]:
        selected = info.selected_pane
        agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        git_data = work.get("git") if isinstance(work.get("git"), dict) else {}
        pull_request = work.get("pull_request") if isinstance(work.get("pull_request"), dict) else None
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
            "linear": work.get("linear") if isinstance(work.get("linear"), list) else [],
            "files": summary.get("files") if isinstance(summary.get("files"), dict) else {},
            "recent_paths": build_recent_agents_payload({session: info}, [session], session_files_by_session={session: files_payload}, locale=locale),
            "latest_summary": truncate_text(latest_summary, 1200),
            "latest_summary_updated_ts": max(0.0, self.float_value(rolling.get("updated_ts"), 0.0)),
            "recent_events": recent_events if recent_events is not None else self.event_log.tail(session=session, limit=5),
            "work": work,
        }

    def activity_summary_payload(self, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        locale = normalize_locale(locale)
        session_names, scope_errors, scope = self.activity_session_names(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        sessions, errors = discover_sessions(session_names)
        errors = [*scope_errors, *errors]
        ordered_sessions = self.tmux_recency_ordered_sessions(session_names)
        self.warm_metadata_cache_async(sessions)
        self.yoagent_controller.prune_yoagent_session_summaries(set(sessions))
        summaries: dict[str, Any] = {}
        ordered_summaries: list[dict[str, Any]] = []
        session_files_by_session: dict[str, SessionFilesPayload] = {}
        transcript_views_by_path: dict[str, dict[str, Any]] = {}
        session_info: dict[str, Any] = {}
        recent_events_by_session = self.event_log.tail_many([session for session in ordered_sessions if session in sessions], limit=5)
        with self.activity_transcript_service.activity_summary_lock:
            if force:
                self.activity_transcript_service.activity_summary_cache.clear()
                self.clear_session_files_cache()
            for session in ordered_sessions:
                info = sessions.get(session)
                if info is None:
                    continue
                work_graph = session_work_graph(info, self.metadata_cache, allow_network=False)
                work = activity_work_summary_from_graph(work_graph)
                files_payload = self.cached_session_files_payload_for_info(info, hours=bounded_hours)
                session_files_by_session[session] = files_payload
                primary_agent = next((item for item in info.agents if item.transcript), None)
                transcript_view: dict[str, Any] | None = None
                if primary_agent is not None and primary_agent.transcript:
                    view_payload, view_status = self.transcript_compact_view(session, 80, info=info, agent_override=primary_agent)
                    if view_status == HTTPStatus.OK:
                        transcript_view = view_payload
                        transcript_views_by_path[str(primary_agent.transcript)] = view_payload
                signature = activity_signature(info, work, files_payload)
                cache_key = (locale, session)
                cached = self.activity_transcript_service.activity_summary_cache.get(cache_key)
                if cached and cached.get("signature") == signature:
                    summary = dict(cached["summary"])
                else:
                    if transcript_view is None:
                        summary = build_session_activity_summary(info, work, files_payload, locale=locale)
                    else:
                        summary = build_session_activity_summary(info, work, files_payload, locale=locale, transcript_view=transcript_view)
                    self.activity_transcript_service.activity_summary_cache[cache_key] = {"signature": signature, "summary": summary}
                    summary = dict(summary)
                self.yoagent_controller.attach_yoagent_session_summary(session, summary)
                summaries[session] = summary
                ordered_summaries.append(summary)
                session_info[session] = self.activity_session_info_payload(
                    session,
                    info,
                    work,
                    files_payload,
                    summary,
                    recent_events=recent_events_by_session.get(session, []),
                    locale=locale,
                )
            for cache_key in list(self.activity_transcript_service.activity_summary_cache):
                if cache_key[1] not in sessions:
                    self.activity_transcript_service.activity_summary_cache.pop(cache_key, None)
        generated = datetime.now(timezone.utc)
        rolling_updated = self.yoagent_controller.latest_yoagent_session_summary_updated_ts()
        with self.yoagent_summary_worker_lock:
            summary_worker = self.yoagent_summary_worker_record
            summary_worker_status = {
                "first_launch_started": summary_worker.first_launch_started,
                "running": summary_worker.running,
            }
        return {
            "generated_at": generated.isoformat(),
            "generated_ts": generated.timestamp(),
            "session_order": [session for session in ordered_sessions if session in summaries],
            "sessions": summaries,
            "session_info": session_info,
            "agents": self.tabber_activity_agents_snapshot(force=force) if not transcript_views_by_path else build_recent_agents_payload(sessions, ordered_sessions, session_files_by_session=session_files_by_session, locale=locale, transcript_views_by_path=transcript_views_by_path),
            "global": build_global_activity_summary(ordered_summaries, errors, locale=locale),
            "capabilities": yoagent_capabilities_payload(locale),
            "errors": errors,
            "locale": locale,
            "session_scope": scope,
            "session_file_hours": bounded_hours,
            "yoagent_summaries": {
                "mode": "first_launch",
                **summary_worker_status,
                "updated_ts": rolling_updated,
                "updated_at": datetime.fromtimestamp(rolling_updated, timezone.utc).isoformat() if rolling_updated else "",
            },
        }
    def float_value(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
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
        except YoagentSkillValidationError as exc:
            return {"kind": kind, "name": name, **skill_validation_payload(exc)}, HTTPStatus.BAD_REQUEST
        except ValueError as exc:
            return {
                "kind": kind,
                "name": name,
                "diagnostic": str(exc),
                **user_message_payload("yoagent.skill.error.invalid", "Invalid skill file."),
            }, HTTPStatus.BAD_REQUEST
        except FileNotFoundError:
            return {
                "kind": kind,
                "name": name,
                **user_message_payload("yoagent.skill.error.notFound", f"Skill file `{name}` was not found.", name=name),
            }, HTTPStatus.NOT_FOUND
        except OSError as exc:
            return {
                "kind": kind,
                "name": name,
                "diagnostic": str(exc),
                **user_message_payload("yoagent.skill.error.readFailed", f"Could not read `{name}`: {exc}", source=name, error=str(exc)),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

    def upsert_yoagent_skill_file(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        kind = str(payload.get("kind") or "skill")
        name = str(payload.get("name") or payload.get("file") or "")
        text = str(payload.get("text") or payload.get("content") or "")
        try:
            item = write_user_skill_file(kind, name, text)
        except YoagentSkillValidationError as exc:
            return {"kind": kind, "name": name, **skill_validation_payload(exc)}, HTTPStatus.BAD_REQUEST
        except ValueError as exc:
            return {
                "kind": kind,
                "name": name,
                "diagnostic": str(exc),
                **user_message_payload("yoagent.skill.error.invalid", "Invalid skill file."),
            }, HTTPStatus.BAD_REQUEST
        except OSError as exc:
            return {
                "kind": kind,
                "name": name,
                "diagnostic": str(exc),
                **user_message_payload("yoagent.skill.error.writeFailed", f"Could not write skill file `{name}`.", name=name),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        self.log_event(
            None,
            "yoagent_skill_file_upserted",
            f"YO!agent skill file updated: {item.get('path')}",
            {
                "kind": item.get("kind"),
                "name": item.get("name"),
                "path": item.get("path"),
            },
            message_key="yoagent.skill.reply.updated",
            message_params={"kind": item.get("kind"), "name": item.get("name"), "path": item.get("path")},
        )
        self.publish_client_event("yoagent_skills_changed", {"kind": item.get("kind"), "name": item.get("name"), "path": item.get("path")}, trigger="yoagent_skill_file", cache="ready")
        return {"ok": True, "file": item, "skills": self.yoagent_skills_payload()}, HTTPStatus.OK

    def delete_yoagent_skill_file(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        kind = str(payload.get("kind") or "skill")
        name = str(payload.get("name") or payload.get("file") or "")
        try:
            item = delete_user_skill_file(kind, name)
        except YoagentSkillValidationError as exc:
            return {"kind": kind, "name": name, **skill_validation_payload(exc)}, HTTPStatus.BAD_REQUEST
        except ValueError as exc:
            return {
                "kind": kind,
                "name": name,
                "diagnostic": str(exc),
                **user_message_payload("yoagent.skill.error.invalid", "Invalid skill file."),
            }, HTTPStatus.BAD_REQUEST
        except FileNotFoundError:
            return {
                "kind": kind,
                "name": name,
                **user_message_payload("yoagent.skill.error.notFound", f"Skill file `{name}` was not found.", name=name),
            }, HTTPStatus.NOT_FOUND
        except OSError as exc:
            return {
                "kind": kind,
                "name": name,
                "diagnostic": str(exc),
                **user_message_payload("yoagent.skill.error.deleteFailed", f"Could not delete skill file `{name}`.", name=name),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        self.log_event(
            None,
            "yoagent_skill_file_deleted",
            f"YO!agent skill file deleted: {item.get('path')}",
            {
                "kind": item.get("kind"),
                "name": item.get("name"),
                "path": item.get("path"),
            },
            message_key="yoagent.skill.reply.deleted",
            message_params={"kind": item.get("kind"), "name": item.get("name"), "path": item.get("path")},
        )
        self.publish_client_event("yoagent_skills_changed", {"kind": item.get("kind"), "name": item.get("name"), "path": item.get("path"), "deleted": True}, trigger="yoagent_skill_file", cache="ready")
        return {"ok": True, "file": item, "skills": self.yoagent_skills_payload()}, HTTPStatus.OK

    def yoagent_skill_file_answer(self, intent: dict[str, Any], locale: str = "en") -> str:
        operation = str(intent.get("operation") or "")
        kind = str(intent.get("kind") or "skill")
        name = str(intent.get("name") or "")
        if operation == "list":
            payload, status = self.yoagent_skill_files_payload()
            if status != HTTPStatus.OK:
                error = yoagent_user_message_text(locale, payload, "common.requestFailed")
                return server_string(locale, "yoagent.skill.reply.listFailed", error=error)
            dirs = payload.get("user_dirs") if isinstance(payload.get("user_dirs"), dict) else {}
            skills_payload = self.yoagent_skills_payload()
            builtin_dirs = skills_payload.get("builtin_dirs") if isinstance(skills_payload.get("builtin_dirs"), dict) else {}
            files = [item for item in payload.get("files", []) if isinstance(item, dict)]
            rows = [server_string(locale, "yoagent.skill.reply.listItem", kind=item.get("kind"), name=item.get("name"), path=item.get("path")) for item in files[:20]]
            body = "\n".join(rows) if rows else server_string(locale, "yoagent.skill.reply.listEmpty")
            return "\n".join([
                server_string(locale, "yoagent.skill.reply.listHeading"),
                "",
                server_string(locale, "yoagent.skill.reply.directory", label=server_string(locale, "yoagent.skill.reply.builtinSkills"), path=builtin_dirs.get("skills") or ""),
                server_string(locale, "yoagent.skill.reply.directory", label=server_string(locale, "yoagent.skill.reply.builtinContext"), path=builtin_dirs.get("context") or ""),
                server_string(locale, "yoagent.skill.reply.directory", label=server_string(locale, "yoagent.skill.reply.userSkills"), path=dirs.get("skills") or ""),
                server_string(locale, "yoagent.skill.reply.directory", label=server_string(locale, "yoagent.skill.reply.userContext"), path=dirs.get("context") or ""),
                "",
                body,
            ])
        if operation == "read":
            payload, status = self.yoagent_skill_files_payload(kind, name)
            if status != HTTPStatus.OK:
                error = yoagent_user_message_text(locale, payload, "common.requestFailed")
                return server_string(locale, "yoagent.skill.reply.readFailed", name=name, error=error)
            item = payload.get("file") if isinstance(payload.get("file"), dict) else {}
            text = truncate_text(str(item.get("text") or ""), 4000)
            return server_string(locale, "yoagent.skill.reply.read", path=item.get("path"), text=text)
        if operation == "delete":
            payload, status = self.delete_yoagent_skill_file({"kind": kind, "name": name})
            if status != HTTPStatus.OK:
                error = yoagent_user_message_text(locale, payload, "common.requestFailed")
                return server_string(locale, "yoagent.skill.reply.deleteFailed", name=name, error=error)
            item = payload.get("file") if isinstance(payload.get("file"), dict) else {}
            return server_string(locale, "yoagent.skill.reply.deleted", kind=item.get("kind"), name=item.get("name"), path=item.get("path"))
        if operation == "upsert":
            payload, status = self.upsert_yoagent_skill_file({"kind": kind, "name": name, "text": intent.get("text") or ""})
            if status != HTTPStatus.OK:
                error = yoagent_user_message_text(locale, payload, "common.requestFailed")
                return server_string(locale, "yoagent.skill.reply.updateFailed", name=name, error=error)
            item = payload.get("file") if isinstance(payload.get("file"), dict) else {}
            return server_string(locale, "yoagent.skill.reply.updated", kind=item.get("kind"), name=item.get("name"), path=item.get("path"))
        return server_string(locale, "yoagent.skill.reply.unknownOperation")

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
                    item.update(message_fields(
                        "status_text",
                        "yoagent.action.status.expired",
                        "action expired; ask again to create a fresh send",
                    ))
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
        detail_rows: list[dict[str, Any]] | None = None,
        response_ms: float | None = None,
        auxiliary_lines: list[str] | None = None,
        auxiliary_preview: str = "",
        stream_items: list[dict[str, Any]] | None = None,
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
        if detail_rows:
            message["detailRows"] = detail_rows
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
        self.publish_background_client_event("yoagent_conversation_changed", {"reason": trigger}, trigger=trigger, cache="ready")

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
        stream_items: list[dict[str, Any]] | None = None,
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

    def sanitized_yoagent_stream_items(self, value: Any) -> list[dict[str, Any]]:
        return sanitized_yoagent_stream_items(value)

    def record_owned_usage_atoms(
        self,
        *,
        provider: str,
        model: str,
        usage: Any,
        source: str,
        event_id: str,
        effort: str = "unknown",
        pricing_profile: str | None = None,
        service_tier: str = "default",
        thread_id: str = "",
        endpoint: str = "",
        opaque_image_tool: bool = False,
        timestamp: float | None = None,
    ) -> bool:
        """Submit structured YOLOmux-owned usage without reading rendered text."""
        provider_name = str(provider or "").strip().lower()
        if provider_name == "openai":
            components = session_files.codex_usage_components(usage)
        elif provider_name == "anthropic":
            components = session_files.claude_record_usage(usage)
        else:
            return False
        recorded_at = float(time.time() if timestamp is None else timestamp)
        if not math.isfinite(recorded_at) or recorded_at <= 0:
            recorded_at = time.time()
        clean_source = str(source or "YOLOmux").strip() or "YOLOmux"
        clean_thread = str(thread_id or "").strip()
        requested_pricing_profile = str(pricing_profile or "").strip().lower() if pricing_profile is not None else None
        selected_pricing_profile = configured_usage_pricing_profile(
            self.settings_payload().get("settings", {}),
            provider=provider_name,
            execution_source=clean_source,
            endpoint=endpoint,
            observed_at=recorded_at,
            requested_profile=requested_pricing_profile,
        )
        if provider_name == "openai" and str(endpoint or "").strip().lower() == "images":
            # Direct Images API usage identifies the exact image model in the
            # structured request/configuration, while its response supplies
            # text/image input and image output counters.  Do not route a
            # Responses image-generation tool through this path: it may not
            # expose the child model or usage envelope.
            atoms = session_files.direct_image_usage_atoms(
                request={"model": str(model or "").strip()},
                response={"usage": usage, "id": str(event_id or "").strip()},
                timestamp=recorded_at,
                source=clean_source,
                request_id=str(event_id or "").strip(),
                root_thread_id=clean_thread or clean_source,
                agent_thread_id=clean_thread or clean_source,
            )
        else:
            atoms = session_files.usage_component_atoms(
                source=clean_source,
                timestamp=recorded_at,
                event_id=str(event_id or "").strip(),
                provider=provider_name,
                model=str(model or "").strip(),
                model_evidence="configured invocation model" if str(model or "").strip() else "unknown",
                effort=effort,
                pricing_profile=selected_pricing_profile,
                service_tier=service_tier,
                components=components,
                root_thread_id=clean_thread or clean_source,
                agent_thread_id=clean_thread or clean_source,
                endpoint=endpoint,
                telemetry_complete=session_files.usage_telemetry_complete(components),
            )
        if opaque_image_tool:
            atoms.extend(session_files.opaque_responses_image_tool_atoms(
                timestamp=recorded_at,
                source=clean_source,
                call_id=str(event_id or "").strip(),
                root_thread_id=clean_thread or clean_source,
                agent_thread_id=clean_thread or clean_source,
            ))
        if not atoms:
            return False
        try:
            if not self.stats_current_client.ensure_started():
                return False
            records = tuple(
                stats_current_usage.usage_atom_from_source(atom)
                for atom in atoms
            )
            for _observations, usage_atoms, _tombstones, _coverage, _unavailable in stats_current_append_batches(
                usage_atoms=records,
            ):
                response = self.stats_current_client.append(
                    usage_atoms=usage_atoms,
                )
                if response.get("ok") is not True:
                    return False
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        return True

    def yoagent_stream_callback(self, stream_id: str, backend: str, *, model: str = "", effort: str = "unknown") -> Any:
        callback = self.yoagent_streams.callback_for(stream_id, backend)
        provider = "openai" if backend == "codex" else "anthropic" if backend == "claude" else ""

        def record(event: dict[str, Any]) -> None:
            callback(event)
            if str(event.get("kind") or event.get("event") or "") != "usage" or not provider:
                return
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
            model_usage = metadata.get("model_usage") if isinstance(metadata.get("model_usage"), dict) else {}
            thread_id = str(event.get("thread_id") or "")
            if usage:
                digest = hashlib.sha256(json.dumps(usage, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
                self.record_owned_usage_atoms(
                    provider=provider, model=model, usage=usage, source="YO!agent", event_id=f"yoagent:{stream_id}:{thread_id}:{digest}",
                    effort=effort, thread_id=thread_id, endpoint="yoagent",
                )
            # Some clients emit a top-level aggregate alongside per-model
            # detail.  The aggregate is authoritative for this turn; using
            # both would bill the same invocation twice.
            for usage_model, model_usage_value in (() if usage else model_usage.items()):
                if not isinstance(model_usage_value, dict):
                    continue
                digest = hashlib.sha256(json.dumps(model_usage_value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
                self.record_owned_usage_atoms(
                    provider=provider, model=str(usage_model or model), usage=model_usage_value, source="YO!agent",
                    event_id=f"yoagent:{stream_id}:{thread_id}:{usage_model}:{digest}", effort=effort, thread_id=thread_id, endpoint="yoagent",
                )

        return record

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        previous_retention_days = settings_payload().get("settings", {}).get("chat", {}).get("retention_days", 7)
        payload = save_settings(patch)
        chat_patch = patch.get("chat") if isinstance(patch, dict) else None
        if isinstance(chat_patch, dict) and "retention_days" in chat_patch:
            retention_days = payload.get("settings", {}).get("chat", {}).get("retention_days", 7)
            self.chat_store.prune_if_due(
                retention_days=retention_days,
                previous_retention_days=previous_retention_days,
            )
        self.sync_tmux_theme_from_settings(payload, force=patch_updates_active_color(patch))
        self.publish_background_client_event("settings_changed", {"mtime_ns": payload.get("mtime_ns", 0), "data": payload}, trigger="manual", cache="ready")
        self.wake_client_event_watcher()
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
        return self.performance_setting_seconds("auto_approve_interval_seconds", 0.1, 4.0)

    def auto_approve_prompt_source(self) -> str:
        value = settings_payload().get("settings", {}).get("yolo", {}).get("prompt_source", "hybrid")
        return value if value in {"pane", "hybrid"} else "hybrid"

    def set_notify(self, enabled: bool) -> dict[str, Any]:
        update_yolomux_state({"notify_enabled": enabled})
        self.log_event(
            None,
            "notify_enabled" if enabled else "notify_disabled",
            "Notify enabled" if enabled else "Notify disabled",
            {},
            message_key="events.message.notify.enabled" if enabled else "events.message.notify.disabled",
        )
        return {"enabled": enabled}

    def log_event(
        self,
        session: str | None,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        message_key: str = "",
        message_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.event_log.append(
            session,
            event_type,
            message,
            details,
            message_key=message_key,
            message_params=message_params,
        )

    def log_auto_event(self, session: str, event_type: str, message: str, details: dict[str, Any]) -> None:
        event_details = dict(details)
        message_key = str(event_details.pop("message_key", "") or "")
        message_params = event_details.pop("message_params", None)
        self.log_event(
            session,
            event_type,
            message,
            event_details,
            message_key=message_key,
            message_params=message_params if isinstance(message_params, dict) else None,
        )

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

    def log_sampled_background_refresh_event(
        self,
        event_type: str,
        role: str,
        message: str,
        details: dict[str, Any],
        *,
        message_key: str,
        message_params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = (event_type, role)
        with self.background_refresh_event_log_lock:
            record = self.background_refresh_event_log_records.setdefault(key, BackgroundRefreshEventLogRecord())
            record.count += 1
            count = record.count
            should_emit = count == 1 or count % BACKGROUND_REFRESH_EVENT_LOG_SAMPLE_EVERY == 0
            if not should_emit:
                return None
            previous_emit_count = record.last_emit_count
            record.last_emit_count = count
        event_details = dict(details)
        event_details["sample_count"] = count
        suppressed = max(0, count - previous_emit_count - 1)
        if suppressed:
            event_details["suppressed_since_last"] = suppressed
        return self.log_event(
            None,
            event_type,
            message,
            event_details,
            message_key=message_key,
            message_params=message_params,
        )

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
                "compute_ms_total": round(float(item["compute_ms_total"]), 3),
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

    def server_cpu_budget_top_consumers(self, limit: int = 3) -> list[dict[str, Any]]:
        """Return bounded endpoint/background owners ranked by aggregate compute."""

        rows = [
            dict(row)
            for row in self.performance_metrics_payload().get("summary", [])
            if isinstance(row, dict) and str(row.get("role") or "")
        ]
        rows.sort(key=lambda row: (
            -float(row.get("compute_ms_total") or 0.0),
            -int(row.get("count") or 0),
            str(row.get("role") or ""),
            str(row.get("surface") or ""),
        ))
        return [
            {
                "role": str(row.get("role") or ""),
                "surface": str(row.get("surface") or ""),
                "count": max(0, int(row.get("count") or 0)),
                "compute_ms_total": round(max(0.0, float(row.get("compute_ms_total") or 0.0)), 3),
            }
            for row in rows[:max(1, int(limit or 3))]
        ]

    def update_server_cpu_budget(self, sample: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """Advance the sustained-CPU warning without adding another sampler."""

        sample_time = float(now if now is not None else sample.get("time") or time.time())
        cpu_percent = max(0.0, self.float_value(sample.get("cpu_percent"), 0.0))
        record = self.stats_collection_state.cpu_budget_record
        record.current_percent = cpu_percent
        if cpu_percent <= SERVER_CPU_BUDGET_PERCENT:
            record.exceeded_since = 0.0
            record.warning_emitted = False
            record.top_consumers = []
            return self.server_cpu_budget_payload(now=sample_time)
        if record.exceeded_since <= 0:
            record.exceeded_since = sample_time
        sustained_seconds = max(0.0, sample_time - record.exceeded_since)
        if sustained_seconds >= SERVER_CPU_BUDGET_SUSTAINED_SECONDS and not record.warning_emitted:
            record.warning_emitted = True
            record.last_warning_at = sample_time
            record.top_consumers = self.server_cpu_budget_top_consumers()
            consumer_text = ", ".join(
                f"{row['role']}:{row['surface']}={row['compute_ms_total']:.1f}ms"
                for row in record.top_consumers
            ) or "no profiled consumers"
            message = (
                f"YOLOmux CPU {cpu_percent:.1f}% exceeded {SERVER_CPU_BUDGET_PERCENT:.0f}% "
                f"for {sustained_seconds:.0f}s; top compute: {consumer_text}"
            )
            emit_server_log(
                "warning", "stats-cpu", message,
                category="performance", dedupe_key="server-cpu-budget", dedupe_seconds=SERVER_CPU_BUDGET_SUSTAINED_SECONDS,
            )
            self.log_event(
                None,
                "server_cpu_budget_warning",
                message,
                {
                    "cpu_percent": round(cpu_percent, 3),
                    "budget_percent": SERVER_CPU_BUDGET_PERCENT,
                    "sustained_seconds": round(sustained_seconds, 3),
                    "top_consumers": json.dumps(record.top_consumers, separators=(",", ":")),
                },
            )
        return self.server_cpu_budget_payload(now=sample_time)

    def server_cpu_budget_payload(self, *, now: float | None = None) -> dict[str, Any]:
        record = self.stats_collection_state.cpu_budget_record
        sample_time = float(now if now is not None else time.time())
        sustained_seconds = max(0.0, sample_time - record.exceeded_since) if record.exceeded_since > 0 else 0.0
        status = "warning" if record.warning_emitted else ("watching" if record.exceeded_since > 0 else "ok")
        return {
            "status": status,
            "current_percent": round(record.current_percent, 3),
            "budget_percent": SERVER_CPU_BUDGET_PERCENT,
            "sustained_budget_seconds": SERVER_CPU_BUDGET_SUSTAINED_SECONDS,
            "sustained_seconds": round(sustained_seconds, 3),
            "exceeded_since": record.exceeded_since,
            "last_warning_at": record.last_warning_at,
            "top_consumers": [dict(row) for row in record.top_consumers],
        }

    def runtime_python_profile(self, duration_seconds: Any = 0.5, interval_seconds: Any = 0.01) -> dict[str, Any]:
        duration = max(0.05, min(self.float_value(duration_seconds, 0.5), 1.0))
        interval = max(0.005, min(self.float_value(interval_seconds, 0.01), 0.1))
        deadline = time.monotonic() + duration
        samples = 0
        thread_rows: dict[int, dict[str, Any]] = {}
        while True:
            threads_by_ident = {
                thread.ident: thread
                for thread in threading.enumerate()
                if thread.ident is not None and thread.native_id is not None
            }
            for ident, frame in sys._current_frames().items():
                thread = threads_by_ident.get(ident)
                if thread is None or thread.native_id is None:
                    continue
                stack = []
                cursor = frame
                while cursor is not None and len(stack) < 10:
                    code = cursor.f_code
                    stack.append(f"{Path(code.co_filename).name}:{code.co_name}:{cursor.f_lineno}")
                    cursor = cursor.f_back
                stack_text = " <- ".join(stack)
                row = thread_rows.setdefault(thread.native_id, {
                    "native_id": thread.native_id,
                    "name": thread.name,
                    "daemon": thread.daemon,
                    "samples": 0,
                    "stacks": {},
                })
                row["samples"] += 1
                row["stacks"][stack_text] = int(row["stacks"].get(stack_text, 0)) + 1
            samples += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
        rows = []
        for row in thread_rows.values():
            stack_rows = [
                {"stack": stack, "samples": count}
                for stack, count in sorted(row.pop("stacks").items(), key=lambda item: (-item[1], item[0]))[:5]
            ]
            row["top_stacks"] = stack_rows
            rows.append(row)
        rows.sort(key=lambda row: int(row["native_id"]))
        return {
            "duration_seconds": duration,
            "interval_seconds": interval,
            "sample_rounds": samples,
            "threads": rows[:64],
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
        with self.session_files_service.cache_lock:
            session_files_refreshing_count = len(self.session_files_service.work_records)
        with self.activity_transcript_service.tabber_cache_lock:
            tabber_activity_refreshing = self.activity_transcript_service.tabber_cache_record.refresh_worker is not None
            tabber_warmer_running = self.activity_transcript_service.tabber_warmer_record.running
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            transcripts_payload_refreshing = self.activity_transcript_service.transcripts_payload_cache_record.worker is not None
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

    def runtime_local_services(self) -> dict[str, Any]:
        """Return bounded worker diagnostics without exposing service payloads."""
        indexd = self.search_indexer.runtime_status()
        current_runtime = self.stats_current_runtime.status()
        current_service = current_runtime.get("service") if isinstance(current_runtime.get("service"), dict) else {}
        migration = current_service.get("migration") if isinstance(current_service.get("migration"), dict) else {}
        build = current_service.get("build") if isinstance(current_service.get("build"), dict) else {}
        service_usage = current_service.get("usage") if isinstance(current_service.get("usage"), dict) else {}
        transcript_usage = self.stats_current_transcript_usage.status()
        token_family = current_runtime.get("families", {}).get("agent_tokens", {}) if isinstance(current_runtime.get("families"), dict) else {}
        token_cadence = float(token_family.get("cadence_seconds") or STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS) if isinstance(token_family, dict) else STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS
        usage = dict(service_usage)
        usage["transcripts"] = transcript_usage
        usage["health"] = stats_current_usage_health(
            service_usage,
            transcript_usage,
            token_cadence,
        )
        statsd = {
            "service": "statsd",
            "pid": int(current_service.get("pid") or 0),
            "started_at": float(current_service.get("started_at") or 0.0),
            "version": int(current_service.get("version") or 0),
            "healthy": current_service.get("ok") is True and migration.get("state") != "failed",
            "clients": int(current_service.get("clients") or 0),
            "queues": current_service.get("queue") if isinstance(current_service.get("queue"), dict) else {},
            "cache": current_service.get("warm") if isinstance(current_service.get("warm"), dict) else {},
            "migration": migration,
            "build": build,
            "delta": current_service.get("delta") if isinstance(current_service.get("delta"), dict) else {},
            "sampler_families": current_runtime.get("families") if isinstance(current_runtime.get("families"), dict) else {},
            "usage": usage,
            "last_failure": str(migration.get("failure") or build.get("last_failure") or ""),
            "resources": {"cpu_percent": None, "rss_bytes": None},
        }
        jobd = self.job_client.runtime_status()
        statusd = self.status_client.runtime_status()
        approvald = self.approval_client.runtime_status()
        rows = [indexd, statsd, jobd, statusd, approvald]
        totals = {"processes": 0, "cpu_percent": 0.0, "rss_bytes": 0}
        now = time.time()
        for row in rows:
            # Derive per-service uptime once, here, from the started_at each
            # runtime_status already reports — one owner for the Local-services
            # table's Uptime cell instead of adding the field to five
            # per-service status builders.
            started_at = float(row.get("started_at") or 0.0)
            row["uptime_seconds"] = max(0.0, now - started_at) if int(row.get("pid") or 0) > 0 and started_at > 0 else None
            if int(row.get("pid") or 0) > 0:
                totals["processes"] += 1
            resources = row.get("resources") if isinstance(row.get("resources"), dict) else {}
            cpu_percent = resources.get("cpu_percent")
            rss_bytes = resources.get("rss_bytes")
            if isinstance(cpu_percent, (int, float)):
                totals["cpu_percent"] += float(cpu_percent)
            if isinstance(rss_bytes, int):
                totals["rss_bytes"] += rss_bytes
        return {"services": rows, "totals": totals}

    def runtime_report_payload(
        self,
        *,
        background_status: dict[str, Any] | None = None,
        owner_debug: dict[str, Any] | None = None,
        owner_control_response: dict[str, Any] | None = None,
        force_transcripts: bool = True,
    ) -> dict[str, Any]:
        status = background_status if isinstance(background_status, dict) else self.background_owner.status_payload()
        # Remote control responses from older servers may still carry perf, while the current
        # topbar status deliberately does not.  Keep the report's diagnostics source explicit.
        diagnostic_status = dict(status)
        if not isinstance(diagnostic_status.get("perf"), dict):
            diagnostic_status.update(self.performance_diagnostics_payload())
        transcript_payload = self.transcripts_payload(force=force_transcripts)
        client_events = self.client_events.snapshot()
        chat_events = {
            event_type: {
                "published": int(client_events.get("published_by_type", {}).get(event_type, {}).get("events", 0)),
                "delivered": int(client_events.get("delivered_by_type", {}).get(event_type, {}).get("events", 0)),
            }
            for event_type in ("chat_messages_changed", "chat_typing_changed")
        }
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
            "search_index": (
                owner_control_response.get("search_index_runtime")
                if isinstance(owner_control_response, dict) and isinstance(owner_control_response.get("search_index_runtime"), dict)
                else file_index.runtime_diagnostics()
            ),
            "local_services": self.runtime_local_services(),
            "top_endpoints": self.runtime_top_endpoints(diagnostic_status),
            "top_background_work": self.runtime_top_background_work(diagnostic_status),
            "top_event_types": self.runtime_top_event_types(),
            "client_events": client_events,
            "chat": {
                **self.chat_service.diagnostics(),
                "subscribers": int(client_events.get("channel_counts", {}).get("chat", 0)),
                "events": chat_events,
            },
            # Privacy-safe login-throttle aggregates: allowed/blocked-by-scope counts,
            # active rows, locked accounts, decision latency — never raw usernames/IPs.
            "login_throttle": {
                **self.login_rate_limiter.diagnostics(),
                "edge": self.login_edge_controller.diagnostics(),
            },
            "largest_active_transcripts": self.runtime_largest_transcripts(transcript_payload),
            "transcripts_cache": transcript_payload.get("cache", {}) if isinstance(transcript_payload, dict) else {},
        }

    def system_status_payload(self) -> dict[str, Any]:
        """Return bounded live diagnostics for the YO!stats System view."""
        # Diagnostics are a reader. Only the CPU family worker may advance the
        # process/host baselines; otherwise a System refresh can consume the
        # next one-second observation and leave no durable bucket for it.
        sample = self.latest_stats_sample()
        return {
            **self.runtime_report_payload(force_transcripts=False),
            "generated_at": time.time(),
            "server": {
                "version": YOLOMUX_VERSION,
                "pid": int(sample.get("pid") or os.getpid()),
                "started_at": float(sample.get("started_at") or SERVER_STARTED_AT),
                "uptime_seconds": float(sample.get("uptime_seconds") or 0.0),
                "cpu_percent": float(sample.get("cpu_percent") or 0.0),
                "system_cpu_percent": float(sample.get("system_cpu_percent") or 0.0),
                "rss_bytes": int(sample.get("rss_bytes") or 0),
            },
            "cpu_budget": self.server_cpu_budget_payload(),
            # System mirrors the same canonical matrix used by the current
            # capabilities endpoint; there is no diagnostic-only policy copy.
            "resolution_capabilities": stats_resolution.wire_capabilities(),
            "stats_current": self.stats_current_runtime.status(),
        }

    def events_payload(self, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session and session not in self.sessions:
            diagnostic = f"unknown session: {session}"
            return user_message_payload("status.sessionEnded", diagnostic, session=session), HTTPStatus.NOT_FOUND
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
        title_key: str = "searchHistory.result.sessionSummary",
        title_params: dict[str, Any] | None = None,
    ) -> SearchResult | None:
        if not str(query or "").strip() or str(query).strip().lower() not in str(text or "").lower():
            return None
        target_type = "activity-summary" if kind == "global_summary" else "summary"
        fallback_title = title or (f"{session} summary" if session else "Global summary")
        return {
            "session": session,
            "timestamp": timestamp,
            "kind": kind,
            "source": source,
            **message_fields("title", title_key, fallback_title, title_params if title_params is not None else {"session": session}),
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
                    title_key="searchHistory.result.sessionSummary",
                    title_params={"session": name},
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
                    title_key="searchHistory.result.rollingSummary",
                    title_params={"session": name},
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
                title_key="searchHistory.result.globalSummary",
                title_params={},
            )
            if result:
                results.append(result)
                legacy_summaries.append({"session": "", "type": "global_summary", "text": truncate_text(global_text, 2000)})
        return results[:limit], legacy_summaries[:limit]

    def search_payload(self, query: str, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session and session not in self.sessions:
            diagnostic = f"unknown session: {session}"
            return user_message_payload("status.sessionEnded", diagnostic, session=session), HTTPStatus.NOT_FOUND
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
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            payload = self.activity_transcript_service.transcripts_payload_cache_record.payload
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
        record = self.input_heartbeat_record
        with record.condition:
            worker = record.worker
            if worker is not None and worker.is_alive():
                return
            record.stop_requested = False
            worker = threading.Thread(target=self.input_heartbeat_worker_loop, name="input-heartbeats", daemon=True)
            record.worker = worker

        def rollback() -> None:
            with record.condition:
                if self.input_heartbeat_record is record and record.worker is worker:
                    record.worker = None
                    record.stop_requested = True

        common.start_thread_with_rollback(worker, rollback)

    def _take_input_heartbeat_batch_locked(self) -> list[PendingInputHeartbeat]:
        record = self.input_heartbeat_record
        batch = list(record.pending.values())
        record.pending.clear()
        record.flush_active = bool(batch)
        return batch

    def _finish_input_heartbeat_flush(self) -> None:
        record = self.input_heartbeat_record
        with record.condition:
            record.flush_active = False
            record.condition.notify_all()

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
        record = self.input_heartbeat_record
        worker = threading.current_thread()
        try:
            while True:
                with record.condition:
                    while (not record.pending or record.flush_active) and not record.stop_requested:
                        record.condition.wait()
                    if record.stop_requested and not record.pending:
                        return
                    record.condition.wait(max(0.0, INPUT_HEARTBEAT_COALESCE_SECONDS))
                    while record.flush_active and not record.stop_requested:
                        record.condition.wait()
                    batch = self._take_input_heartbeat_batch_locked()
                try:
                    self.flush_input_heartbeat_batch(batch)
                finally:
                    self._finish_input_heartbeat_flush()
        finally:
            with record.condition:
                if record.worker is worker:
                    record.worker = None
                record.condition.notify_all()

    def flush_input_heartbeats(self, timeout: float = 1.0) -> bool:
        record = self.input_heartbeat_record
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with record.condition:
                while record.flush_active:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    record.condition.wait(remaining)
                if not record.pending:
                    return True
                batch = self._take_input_heartbeat_batch_locked()
            try:
                self.flush_input_heartbeat_batch(batch)
            finally:
                self._finish_input_heartbeat_flush()

    def stop_input_heartbeat_worker(self) -> None:
        record = self.input_heartbeat_record
        with record.condition:
            record.stop_requested = True
            record.condition.notify_all()
            worker = record.worker
        self.flush_input_heartbeats()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        self.flush_input_heartbeats()

    def record_user_input(self, session: str, byte_count: int, source: str = "host", data: str = "") -> None:
        """Queue one user-input heartbeat from the WS bridge without touching tmux or disk."""
        clean_session = str(session or "").strip()
        if not clean_session:
            return
        if data and not terminal_input_counts_as_user_activity(data):
            return
        record = self.input_heartbeat_record
        self.start_input_heartbeat_worker()
        clean_source = str(source or "host")
        count = max(0, int(byte_count or 0))
        with record.condition:
            key = (clean_session, clean_source)
            pending = record.pending.get(key)
            if pending is None:
                record.pending[key] = PendingInputHeartbeat(clean_session, clean_source, count, time.time())
            else:
                pending.byte_count += count
                pending.ts = time.time()
            record.condition.notify()

    def tabber_activity_session_source_signature(
        self,
        info: SessionInfo,
        files_payload: SessionFilesPayload | dict[str, Any],
        activity_snapshot: dict[str, Any],
        preclassified_by_target: dict[str, dict[str, Any]],
        attention_ack_rev: int,
    ) -> str:
        activity_rows = {
            key: value
            for key, value in activity_snapshot.items()
            if key == info.session or key.startswith(f"{info.session}:")
        }
        pane_rows = [
            (
                pane.target,
                pane.window,
                pane.pane,
                pane.current_path,
                pane.command,
                pane.process_label or "",
                pane.pid,
                pane.active,
                pane.window_active,
            )
            for pane in info.panes
        ]
        screen_rows = []
        for agent in info.agents:
            target = str(agent.pane_target or "")
            screen = preclassified_by_target.get(target, {})
            state = self.agent_window_state_from_screen(screen)
            screen_rows.append((target, state, self.agent_window_attention_signature(state, screen)))
        signature_payload = {
            "info": session_info_cache_signature(info),
            "panes": pane_rows,
            "files": self.session_files_payload_signature(files_payload),
            "activity": activity_rows,
            "screens": screen_rows,
            "attention_ack_rev": attention_ack_rev,
        }
        return self.stable_client_event_payload_signature(signature_payload)

    def build_activity_payload(self, session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        session_names, scope_errors, scope = self.activity_session_names(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        sessions, errors = discover_sessions(session_names)
        errors = [*scope_errors, *errors]
        ordered_sessions = self.tmux_recency_ordered_sessions(session_names)
        agent_infos = {session: sessions[session] for session in ordered_sessions if session in sessions and sessions[session].agents}
        session_files_by_session = self.cached_session_files_payloads_for_infos(agent_infos, hours=bounded_hours)
        activity_snapshot = self.activity_snapshot_with_recency()
        # Auto-approve owns prompt/screen classification.  Reuse its immutable roster rows
        # here so activity cannot publish a contradictory state for the same observation.
        # Do not make activity's cold path synchronously build a second roster.  At startup the
        # roster refresh owns the first classification; until it commits, activity keeps its
        # existing path and joins the owned revision on the next refresh.
        roster_payload = self.status_snapshot_payload()
        snapshot_revision, owned_agent_rows = (
            self.agent_window_snapshot_rows_by_target(roster_payload)
            if roster_payload is not None
            else (0, {})
        )
        self.merge_shared_attention_acks()
        with self.client_watch_service.lock:
            attention_ack_rev = self.client_watch_service.attention_ack_rev
        preclassified_by_session: dict[str, dict[str, dict[str, Any]]] = {}
        session_signatures: dict[str, str] = {}
        for session, info in agent_infos.items():
            screens = {
                str(agent.pane_target or ""): self.agent_window_screen_state(agent)
                for agent in info.agents
                if agent.pane_target
            }
            preclassified_by_session[session] = screens
            session_signatures[session] = self.tabber_activity_session_source_signature(
                info,
                session_files_by_session.get(session, {}),
                activity_snapshot,
                screens,
                attention_ack_rev,
            )
        with self.activity_transcript_service.tabber_cache_lock:
            record = self.activity_transcript_service.tabber_cache_record
            can_reuse = record.session_scope == scope and record.session_file_hours == bounded_hours
            previous_signatures = dict(record.session_signatures) if can_reuse else {}
            previous_rows = copy.deepcopy(record.session_rows) if can_reuse else {}
        session_rows: dict[str, dict[str, Any]] = {}
        rebuilt = 0
        reused = 0
        for session, info in agent_infos.items():
            signature = session_signatures[session]
            previous = previous_rows.get(session)
            if previous_signatures.get(session) == signature and isinstance(previous, dict):
                session_rows[session] = previous
                reused += 1
                continue
            files_payload = session_files_by_session.get(session, {})
            transcript_views_by_path: dict[str, dict[str, Any]] = {}
            for agent in info.agents:
                if not agent.transcript:
                    continue
                view_payload, view_status = self.transcript_compact_view(session, 80, info=info, agent_override=agent)
                if view_status == HTTPStatus.OK:
                    transcript_views_by_path[str(agent.transcript)] = view_payload
            session_rows[session] = {
                "agents": build_recent_agents_payload(
                    {session: info},
                    [session],
                    session_files_by_session={session: files_payload},
                    transcript_views_by_path=transcript_views_by_path,
                ),
                "agent_windows": self.agent_window_status_payloads(
                    session,
                    info=info,
                    discovered_sessions=sessions,
                    activity_snapshot=activity_snapshot,
                    preclassified_by_target=preclassified_by_session.get(session),
                    files_payload=files_payload,
                    owned_rows_by_target=owned_agent_rows,
                    snapshot_revision=snapshot_revision,
                ),
            }
            rebuilt += 1
        agents = [
            agent
            for session in ordered_sessions
            for agent in session_rows.get(session, {}).get("agents", [])
            if isinstance(agent, dict)
        ]
        agent_windows = {
            session: copy.deepcopy(session_rows[session].get("agent_windows", []))
            for session in ordered_sessions
            if session in session_rows
        }
        with self.activity_transcript_service.tabber_cache_lock:
            record = self.activity_transcript_service.tabber_cache_record
            record.session_scope = scope
            record.session_file_hours = bounded_hours
            record.session_signatures = dict(session_signatures)
            record.session_rows = copy.deepcopy(session_rows)
        self.record_performance_sample(
            BACKGROUND_ROLE_TABBER_ACTIVITY,
            "row-refresh",
            trigger="build",
            count=rebuilt,
            cache_key={"kind": "tabber-activity"},
            cache_status="reused" if rebuilt == 0 else "partial" if reused else "rebuilt",
            details={"rebuilt": rebuilt, "reused": reused, "removed": len(set(previous_rows) - set(session_rows))},
        )
        return {
            "activity": activity_snapshot,
            "agents": agents,
            "agent_windows": agent_windows,
            "errors": errors,
            "session_scope": scope,
            "session_file_hours": bounded_hours,
            "agent_window_snapshot_revision": snapshot_revision,
        }

    def tabber_activity_source_signature(self, session_scope: Any = "configured") -> str:
        # Acknowledgements change agent-window visibility without changing the process or
        # transcript identity below. Fold the durable revision into this cache key so every
        # server stops serving an earlier unacknowledged Tabber snapshot immediately.
        self.merge_shared_attention_acks()
        with self.client_watch_service.lock:
            attention_ack_rev = self.client_watch_service.attention_ack_rev
        session_names, _scope_errors, scope = self.activity_session_names(session_scope)
        sessions, _errors = discover_sessions(session_names)
        tmux_signature = self.stable_client_event_payload_signature(
            self.tmux_signal_signature_payload(self.tmux_signal_snapshot())
        )
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
                tuple(
                    (
                        pane.target,
                        pane.window,
                        pane.pane,
                        pane.current_path,
                        pane.process_label or "",
                        pane.pid,
                        pane.active,
                        pane.window_active,
                    )
                    for pane in info.panes
                ),
            ))
        key_text = self.client_event_payload_signature(
            {
                "scope": scope,
                "sessions": rows,
                "attention_ack_rev": attention_ack_rev,
                "tmux_signature": tmux_signature,
            }
        )
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
        with self.activity_transcript_service.tabber_cache_lock:
            self.activity_transcript_service.tabber_cache_record.stored_at = time.monotonic() if stored_at is None else stored_at
            self.activity_transcript_service.tabber_cache_record.payload = copy.deepcopy(payload)
            self.activity_transcript_service.tabber_cache_record.source_signature = source_signature
        if write_disk:
            self.write_tabber_activity_disk_cache(payload, source_signature=source_signature)

    def get_tabber_activity_cache(self, max_age_seconds: float, allow_stale: bool = True, hours: float | None = None, source_signature: str = "") -> tuple[dict[str, Any], bool, float] | None:
        started = time.perf_counter()
        now = time.monotonic()
        bounded_hours = session_files.bounded_session_files_hours(24.0 if hours is None else hours)
        stale_cached: tuple[dict[str, Any], bool, float] | None = None
        with self.activity_transcript_service.tabber_cache_lock:
            record = self.activity_transcript_service.tabber_cache_record
            if record.stored_at is not None and record.payload is not None:
                stored_at = record.stored_at
                payload = record.payload
                cached_hours = session_files.bounded_session_files_hours(self.float_value(payload.get("session_file_hours"), 24.0))
                if cached_hours == bounded_hours and (not source_signature or record.source_signature == source_signature):
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
        bounded_hours = session_files.bounded_session_files_hours(self.float_value(hours, 24.0))
        source_signature = self.tabber_activity_source_signature()
        inflight_key = (bounded_hours, source_signature)
        with self.activity_transcript_service.tabber_cache_lock:
            future = self.activity_transcript_service.tabber_cache_record.inflight_by_key.get(inflight_key)
            if future is None:
                future = Future()
                self.activity_transcript_service.tabber_cache_record.inflight_by_key[inflight_key] = future
                owner = True
            else:
                owner = False
        if not owner:
            payload = future.result()
            self.record_performance_sample(
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "refresh",
                trigger="single-flight",
                compute_ms=0,
                payload=payload,
                cache_key={"kind": "tabber-activity"},
                cache_status="coalesced",
                cache_hit=True,
                cache_fresh=True,
            )
            return copy.deepcopy(payload)
        try:
            payload = self.refresh_tabber_activity_cache_owner(bounded_hours, source_signature)
            future.set_result(copy.deepcopy(payload))
            return payload
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.activity_transcript_service.tabber_cache_lock:
                if self.activity_transcript_service.tabber_cache_record.inflight_by_key.get(inflight_key) is future:
                    self.activity_transcript_service.tabber_cache_record.inflight_by_key.pop(inflight_key, None)

    def refresh_tabber_activity_cache_owner(self, bounded_hours: float, source_signature: str) -> dict[str, Any]:
        started = time.perf_counter()
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
        with self.activity_transcript_service.tabber_cache_lock:
            record = self.activity_transcript_service.tabber_cache_record
            current_payload = copy.deepcopy(record.payload) if record.payload is not None else None
            current_signature = record.source_signature
        if current_payload is not None and current_signature == source_signature:
            self.record_performance_sample(
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "refresh",
                trigger="owner",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=current_payload,
                cache_key={"kind": "tabber-activity"},
                cache_status="hit:unchanged",
                cache_hit=True,
                cache_fresh=True,
            )
            return current_payload
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

    def run_tabber_activity_cache_refresh(self, worker: threading.Thread) -> None:
        try:
            started = time.perf_counter()
            refresh_details = self.background_refresh_event_details(BACKGROUND_ROLE_TABBER_ACTIVITY, {"cache_key_kind": "tabber-activity"}, cache_key={"kind": "tabber-activity"})
            self.log_sampled_background_refresh_event(
                "background_refresh_started",
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "Tabber activity background refresh started",
                refresh_details,
                message_key="events.message.backgroundRefresh.started",
                message_params={"target": message_descriptor("tabber.title", "Tabber")},
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
                message_key="events.message.backgroundRefresh.finished",
                message_params={"target": message_descriptor("tabber.title", "Tabber")},
            )
            self.publish_background_refresh_done(BACKGROUND_ROLE_TABBER_ACTIVITY, {"compute_ms": compute_ms})
        finally:
            with self.activity_transcript_service.tabber_cache_lock:
                if self.activity_transcript_service.tabber_cache_record.refresh_worker is worker:
                    self.activity_transcript_service.tabber_cache_record.refresh_worker = None

    def start_tabber_activity_cache_refresh(self) -> bool:
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "async-refresh"})
            return False
        with self.activity_transcript_service.tabber_cache_lock:
            if self.activity_transcript_service.tabber_cache_record.refresh_worker is not None:
                return False
            worker: threading.Thread

            def run_refresh() -> None:
                self.run_tabber_activity_cache_refresh(worker)

            worker = threading.Thread(target=run_refresh, name="tabber-activity-refresh", daemon=True)
            self.activity_transcript_service.tabber_cache_record.refresh_worker = worker
        def rollback() -> None:
            with self.activity_transcript_service.tabber_cache_lock:
                if self.activity_transcript_service.tabber_cache_record.refresh_worker is worker:
                    self.activity_transcript_service.tabber_cache_record.refresh_worker = None

        common.start_thread_with_rollback(worker, rollback)
        return True

    def start_tabber_activity_cache_warmer(self) -> bool:
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "warmer"})
            return False
        with self.activity_transcript_service.tabber_cache_lock:
            current = self.activity_transcript_service.tabber_warmer_record
            if current.running and current.thread is not None and current.thread.is_alive():
                return False
            record = TabberActivityWarmerRecord(running=True, consumer_until=current.consumer_until)
            worker = threading.Thread(target=self.tabber_activity_cache_warmer_loop, args=(record,), name="tabber-activity-cache", daemon=True)
            record.thread = worker
            self.activity_transcript_service.tabber_warmer_record = record

        def rollback() -> None:
            with self.activity_transcript_service.tabber_cache_lock:
                if self.activity_transcript_service.tabber_warmer_record is record and record.thread is worker:
                    record.thread = None
                    record.running = False

        common.start_thread_with_rollback(worker, rollback)
        return True

    def tabber_activity_cache_warmer_loop(self, record: TabberActivityWarmerRecord) -> None:
        try:
            while True:
                with self.activity_transcript_service.tabber_cache_lock:
                    if self.activity_transcript_service.tabber_warmer_record is not record or not record.running:
                        return
                if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
                    return
                started = time.monotonic()
                try:
                    if self.tabber_activity_has_recent_consumer():
                        self.refresh_tabber_activity_cache()
                    else:
                        self.record_performance_sample(
                            BACKGROUND_ROLE_TABBER_ACTIVITY,
                            "warmer",
                            trigger="idle",
                            cache_key={"kind": "tabber-activity"},
                            cache_status="skipped:no-consumer",
                        )
                        # Demand expired: park instead of ticking idle forever.
                        # Clear-then-recheck closes the race with a returning
                        # consumer: a mark before the recheck extends the
                        # deadline (we skip the wait); one after it sets the
                        # event (the wait returns immediately).
                        record.wake.clear()
                        with self.activity_transcript_service.tabber_cache_lock:
                            if self.activity_transcript_service.tabber_warmer_record is not record or not record.running:
                                return
                            parked = record.consumer_until <= time.monotonic()
                        if parked:
                            record.wake.wait()
                        continue
                except (OSError, RuntimeError, ValueError) as exc:
                    self.log_event(
                        None,
                        "client_event_watch_error",
                        f"Tabber activity cache refresh failed: {exc}",
                        {"diagnostic": str(exc)},
                        message_key="events.message.tabberActivity.refreshFailed",
                    )
                interval = self.tabber_activity_refresh_seconds()
                elapsed = max(0.0, time.monotonic() - started)
                time.sleep(max(0.1, interval - elapsed))
        finally:
            with self.activity_transcript_service.tabber_cache_lock:
                if self.activity_transcript_service.tabber_warmer_record is record:
                    record.running = False

    def empty_tabber_activity_payload(self, bounded_hours: float, refresh_seconds: float, **cache: Any) -> dict[str, Any]:
        return {
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
                **cache,
            },
        }

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
            return self.empty_tabber_activity_payload(bounded_hours, refresh_seconds, idle_no_consumer=True), HTTPStatus.OK
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
            return self.empty_tabber_activity_payload(bounded_hours, refresh_seconds, refreshing_elsewhere=True), HTTPStatus.OK
        refreshing = self.start_tabber_activity_cache_refresh()
        return self.empty_tabber_activity_payload(bounded_hours, refresh_seconds, refreshing=refreshing), HTTPStatus.OK

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
        work = activity_work_summary_from_graph(session_work_graph(info, self.metadata_cache, allow_network=False))
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
        pull_request = compact_pull_request_for_history(work.get("pull_request") if isinstance(work, dict) else None)
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
            "work": work,
            "recent_events": self.event_log.tail(session=session, limit=5),
        }

    def run_history_payload(self, session: str | None = None) -> tuple[RunHistoryPayload, HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        store = self.run_history_store_for_app()
        stored_before = store.load_rows(session=session)
        if session and session not in self.sessions and not stored_before:
            diagnostic = f"unknown session: {session}"
            return user_message_payload("status.sessionEnded", diagnostic, session=session), HTTPStatus.NOT_FOUND
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
        issues = [message_fields("message", "searchHistory.error.discovery", error, {"error": error}) for error in [*refresh_errors, *errors]]
        return {"session": session or "", "runs": rows, "errors": issues}, HTTPStatus.OK

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
            diagnostic = f"unknown session: {session}"
            return {"session": session, **user_message_payload("status.sessionEnded", diagnostic, session=session)}, HTTPStatus.NOT_FOUND
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
                diagnostic = f"unknown session: {session}"
                payloads[session] = {"session": session, "errors": [], **user_message_payload("status.sessionEnded", diagnostic, session=session)}
                statuses[session] = int(HTTPStatus.NOT_FOUND)
                continue
            info = infos.get(session)
            if info is None:
                diagnostic = f"session unavailable: {session}"
                payloads[session] = {"session": session, "errors": [], **user_message_payload("diff.error.sessionUnavailable", diagnostic, session=session)}
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
            for session, info in batch_infos.items():
                payload, status = load_session_payload(session, info)
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
            diagnostic = f"unknown session: {session}"
            return user_message_payload("yoagent.error.unknownSession", diagnostic, session=session), HTTPStatus.NOT_FOUND
        event_type = event.get("type")
        message = event.get("message")
        if not isinstance(event_type, str) or not event_type:
            return user_message_payload("common.requestFailed", "missing event type"), HTTPStatus.BAD_REQUEST
        if not isinstance(message, str) or not message:
            return user_message_payload("common.requestFailed", "missing event message"), HTTPStatus.BAD_REQUEST
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
            return {"ok": True, "status": payload, "search_index_runtime": file_index.runtime_diagnostics()}
        if action == "runtime_profile":
            return {
                "ok": True,
                "profile": self.runtime_python_profile(request.get("duration_seconds"), request.get("interval_seconds")),
            }
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
            diagnostic = f"unknown session: {session}"
            return {"ok": False, **user_message_payload("status.sessionEnded", diagnostic, session=session)}
        records = self.approval_client.status_session(session)
        if not records:
            diagnostic = "YOLO was not enabled here"
            return {
                "ok": True,
                "session": session,
                "enabled": False,
                **user_message_payload("status.yoloAlreadyDisabledFor", diagnostic, session=session),
            }
        # approvald confirms the worker thread exited and released its flock before returning ok.
        released = bool(self.approval_client.stop_session(session).get("ok"))
        if not released:
            diagnostic = "YOLO worker did not stop in time"
            self.log_event(
                session,
                "yolo_release_timeout",
                diagnostic,
                {"requester": requester},
                message_key="events.message.yolo.releaseTimeout",
            )
            return {
                "ok": False,
                "session": session,
                **user_message_payload("status.yoloReleaseFailed", diagnostic, session=session),
            }
        self.log_event(
            session,
            "yolo_released",
            "YOLO released for another server",
            {"requester": requester},
            message_key="events.message.yolo.released",
        )
        self.commit_auto_approve_change(session, enabled=False, trigger="takeover-release")
        return {"ok": True, "session": session, "enabled": False}

    def build_session_metadata_payload(self, lightweight: bool = False) -> dict[str, Any]:
        refresh_errors = self.refresh_sessions(maintenance=not lightweight)
        sessions, errors = discover_sessions(self.sessions)
        with metadata_build_cache():
            session_payloads = {
                name: session_to_json(info, self.metadata_cache, allow_network=False, include_metadata=not lightweight)
                for name, info in sessions.items()
            }
            indexed_repos = [] if lightweight else indexed_repo_summaries(
                cache=self.metadata_cache,
                allow_network=False,
                repo_roots=self.indexed_repo_roots_snapshot(),
            )
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

    def indexed_repo_roots_snapshot(self) -> list[str]:
        """Return the last jobd discovery immediately and advance it asynchronously."""
        raw_dirs = self.settings_payload().get("settings", {}).get("file_explorer", {}).get("indexed_dirs", [])
        indexed_dirs = tuple(str(item).strip() for item in raw_dirs if isinstance(item, str) and str(item).strip()) if isinstance(raw_dirs, list) else ()
        service = self.activity_transcript_service
        now = time.monotonic()
        with service.indexed_repo_lock:
            record = service.indexed_repo_record
            if record.indexed_dirs != indexed_dirs:
                record.indexed_dirs = indexed_dirs
                record.roots = []
                record.refreshed_at = 0.0
                record.retry_at = 0.0
            roots = list(record.roots)
            should_start = (
                record.worker is None
                and now >= record.retry_at
                and (record.refreshed_at <= 0.0 or now - record.refreshed_at >= INDEXED_REPO_ROOTS_CACHE_SECONDS)
            )
            if should_start:
                worker = threading.Thread(
                    target=self.refresh_indexed_repo_roots_worker,
                    args=(indexed_dirs,),
                    name="yolomux-indexed-repos",
                    daemon=True,
                )
                record.worker = worker
                worker.start()
        return roots

    def refresh_indexed_repo_roots_worker(self, indexed_dirs: tuple[str, ...]) -> None:
        """Submit and observe one discovery job without blocking metadata requests."""
        service = self.activity_transcript_service
        worker = threading.current_thread()
        succeeded = False
        try:
            signature = "\0".join(indexed_dirs).encode("utf-8")
            generation = max(1, int(time.time() // INDEXED_REPO_ROOTS_CACHE_SECONDS))
            response = self.job_client.submit(
                "indexed_repo_roots",
                {"indexed_dirs": list(indexed_dirs)},
                priority="maintenance",
                generation=generation,
                coalesce_key=f"indexed-repos:{hashlib.sha256(signature).hexdigest()[:24]}:{generation}",
                deadline_ms=120_000,
            )
            job = response.get("job") if isinstance(response.get("job"), dict) else {}
            job_id = job.get("job_id") if response.get("ok") and isinstance(job.get("job_id"), str) else ""
            if not job_id:
                return
            with service.indexed_repo_lock:
                if service.indexed_repo_record.indexed_dirs != indexed_dirs:
                    return
                service.indexed_repo_record.job_id = job_id
            while True:
                with service.indexed_repo_lock:
                    if service.indexed_repo_record.indexed_dirs != indexed_dirs:
                        return
                response = self.job_client.result(job_id)
                job = response.get("job") if isinstance(response.get("job"), dict) else {}
                status = str(job.get("status") or "")
                if status == "completed" and isinstance(job.get("result"), dict):
                    roots = job["result"].get("roots")
                    safe_roots = [str(item) for item in roots if isinstance(item, str)] if isinstance(roots, list) else []
                    with service.indexed_repo_lock:
                        if service.indexed_repo_record.indexed_dirs == indexed_dirs:
                            service.indexed_repo_record.roots = safe_roots
                            service.indexed_repo_record.refreshed_at = time.monotonic()
                            succeeded = True
                    return
                if status in {"failed", "cancelled", "superseded", "timed_out"} or not response.get("ok"):
                    return
                time.sleep(0.1)
        finally:
            with service.indexed_repo_lock:
                record = service.indexed_repo_record
                if record.worker is worker:
                    record.worker = None
                    record.job_id = ""
                    if not succeeded:
                        record.retry_at = time.monotonic() + 5.0

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
        generation = self.begin_transcripts_payload_work(None, replace=True)
        payload = self.build_session_metadata_payload()
        self.commit_transcripts_payload_cache(payload, generation)
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
            previous_signatures = self.metadata_badge_signature_snapshot_locked()
            for session, next_signature in list(next_signatures.items()):
                previous_signature = self.metadata_badge_records.get(session)
                previous_signature = previous_signature.signature if previous_signature else None
                if previous_signature and self.metadata_badge_change_is_cold_cache_degradation(previous_signature, next_signature):
                    next_signatures[session] = previous_signature

            for session in list(self.metadata_badge_records):
                if session not in next_signatures:
                    self.metadata_badge_records.pop(session, None)
                    continue
                record = self.metadata_badge_records[session]
                record.pulse_until = {badge: until for badge, until in record.pulse_until.items() if until > now}

            for session, next_signature in next_signatures.items():
                record = self.metadata_badge_records.get(session)
                if record is None:
                    self.metadata_badge_records[session] = MetadataBadgeRecord(signature=next_signature, pulse_until={})
                    continue
                previous_signature = record.signature
                for badge in METADATA_BADGES:
                    if self.metadata_badge_change_should_pulse(previous_signature, next_signature, badge):
                        record.pulse_until[badge] = now + self.notification_transition_seconds()
                record.signature = next_signature

            for session, payload in session_payloads.items():
                badge_times = self.metadata_badge_records[session].pulse_until
                remaining = {
                    badge: max(1, int((until - now) * 1000))
                    for badge, until in badge_times.items()
                    if until > now
                }
                if remaining:
                    payload["metadata_badge_pulse_remaining_ms"] = remaining

            if self.metadata_badge_signature_snapshot_locked() != previous_signatures:
                self.persist_metadata_badge_state_locked()

    def load_metadata_badge_state(self) -> None:
        state = read_yolomux_state()
        with self.metadata_badge_lock:
            signatures = self.sanitized_metadata_badge_signatures(state.get(METADATA_BADGE_SIGNATURES_STATE_KEY))
            self.metadata_badge_records = {
                session: MetadataBadgeRecord(signature=signature, pulse_until={})
                for session, signature in signatures.items()
            }

    def metadata_badge_signature_snapshot_locked(self) -> dict[str, dict[str, str]]:
        return {session: dict(record.signature) for session, record in self.metadata_badge_records.items()}

    def persist_metadata_badge_state_locked(self) -> None:
        update_yolomux_state(
            {
                METADATA_BADGE_SIGNATURES_STATE_KEY: self.metadata_badge_signature_snapshot_locked(),
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

    def metadata_badge_signatures_for_session(self, payload: dict[str, Any]) -> dict[str, str]:
        work_graph = as_dict(payload.get("work_graph"))
        work = activity_work_summary_from_graph(work_graph)
        git_data = work.get("git") if isinstance(work.get("git"), dict) else {}
        pr = self.metadata_badge_pull_request(work)
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

    def metadata_badge_pull_request(self, work: dict[str, Any]) -> dict[str, Any]:
        pr = work.get("pull_request")
        if isinstance(pr, dict) and pr.get("number"):
            return pr
        git_data = work.get("git") if isinstance(work.get("git"), dict) else {}
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
            if self.metadata_warm_record.worker is not None:
                return
            snapshot = dict(sessions)
            stop_event = threading.Event()
            worker = threading.Thread(target=self.warm_metadata_cache, args=(snapshot, stop_event), name="metadata-warm", daemon=True)
            record = MetadataWarmRecord(worker=worker, stop_event=stop_event)
            self.metadata_warm_record = record

        def rollback() -> None:
            with self.metadata_warm_lock:
                if self.metadata_warm_record is record and record.worker is worker:
                    record.stop_event.set()
                    self.metadata_warm_record = MetadataWarmRecord()

        common.start_thread_with_rollback(worker, rollback)

    def warm_metadata_cache(self, sessions: dict[str, SessionInfo], stop_event: threading.Event) -> None:
        refresh_needed = False
        try:
            with metadata_build_cache():
                for info in sessions.values():
                    if stop_event.is_set():
                        break
                    session_work_graph(info, self.metadata_cache, allow_network=True)
                    if stop_event.is_set():
                        break
                    # The foreground payload intentionally avoids GitHub work. Once this worker
                    # fills that cache, rebuild only when the canonical graph actually changed;
                    # otherwise a warm build would leave YO!info showing its stale no-PR graph
                    # until a later unrelated refresh, or continuously schedule itself.
                    enriched_graph = session_work_graph(info, self.metadata_cache, allow_network=False)
                    with self.activity_transcript_service.transcripts_payload_cache_lock:
                        cached_payload = self.activity_transcript_service.transcripts_payload_cache_record.payload
                        cached_session = cached_payload.get("sessions", {}).get(info.session) if isinstance(cached_payload, dict) and isinstance(cached_payload.get("sessions"), dict) else None
                        cached_graph = cached_session.get("work_graph") if isinstance(cached_session, dict) else None
                    if isinstance(cached_graph, dict) and self.work_graph_refresh_signature(cached_graph) != self.work_graph_refresh_signature(enriched_graph):
                        refresh_needed = True
        except (OSError, RuntimeError, ValueError) as exc:
            self.log_event(None, "metadata_warm_failed", str(exc)[:512], {"error": type(exc).__name__})
        finally:
            with self.metadata_warm_lock:
                if self.metadata_warm_record.worker is threading.current_thread():
                    self.metadata_warm_record = MetadataWarmRecord()
        if refresh_needed and not stop_event.is_set():
            self.start_transcripts_payload_refresh(publish=True, defer=True)

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
            diagnostic = "no agent transcript found"
            return {"session": session, "errors": errors, **user_message_payload("transcript.noAgentFound", diagnostic)}, HTTPStatus.NOT_FOUND
        agent = next((item for item in info.agents if item.transcript), info.agents[0])
        if not agent.transcript:
            diagnostic = str(agent.error or "no agent transcript found")
            return {
                "session": session,
                "agent": asdict(agent),
                "errors": errors,
                **user_message_payload("transcript.error.unavailable", diagnostic, error=diagnostic),
            }, HTTPStatus.NOT_FOUND
        path = Path(agent.transcript)
        safe_lines = min(max(1, lines), MAX_TRANSCRIPT_TAIL_LINES)
        try:
            stat_signature = file_stat_signature(path)
        except OSError as exc:
            diagnostic = str(exc)
            return {
                "session": session,
                "agent": asdict(agent),
                **user_message_payload("transcript.error.readFailed", diagnostic, error=diagnostic),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        cache_key = (
            session,
            safe_lines,
            stat_signature,
            agent.kind or "",
            agent.session_id or "",
            agent.status or "",
        )
        with self.activity_transcript_service.transcript_tail_cache_lock:
            cached_text = self.activity_transcript_service.transcript_tail_cache.get(cache_key)
            text = cached_text[1] if cached_text else None
        if text is None:
            try:
                text = tail_file_lines(path, safe_lines)
            except OSError as exc:
                diagnostic = str(exc)
                return {
                    "session": session,
                    "agent": asdict(agent),
                    **user_message_payload("transcript.error.readFailed", diagnostic, error=diagnostic),
                }, HTTPStatus.INTERNAL_SERVER_ERROR
            with self.activity_transcript_service.transcript_tail_cache_lock:
                self.cache_set_limited(self.activity_transcript_service.transcript_tail_cache, cache_key, (time.monotonic(), text), TRANSCRIPT_TAIL_CACHE_MAX_ITEMS)
        return {
            "session": session,
            "agent": asdict(agent),
            "path": str(path),
            "lines": safe_lines,
            "text": text,
            "errors": errors,
        }, HTTPStatus.OK

    def transcript_compact_view(
        self,
        session: str,
        messages: int,
        *,
        compact_lines: int = 0,
        since: datetime | None = None,
        info: SessionInfo | None = None,
        agent_override: AgentInfo | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        """Return cached compact facts, scheduling bounded parsing in jobd.

        This selector is deliberately the only request-path bridge to the
        transcript parser.  It keys results by file identity plus byte
        generation, never retains raw transcript text, and degrades to a
        stable pending payload if jobd is unavailable.
        """
        errors: list[str] = []
        if info is None:
            sessions, errors = discover_sessions([session])
            info = sessions.get(session)
        if not info or not info.agents:
            diagnostic = "no agent transcript found"
            return {"session": session, "errors": errors, **user_message_payload("transcript.noAgentFound", diagnostic)}, HTTPStatus.NOT_FOUND
        agent = agent_override or next((item for item in info.agents if item.transcript), info.agents[0])
        if not agent.transcript:
            diagnostic = str(agent.error or "no agent transcript found")
            return {"session": session, "agent": asdict(agent), "errors": errors, **user_message_payload("transcript.error.unavailable", diagnostic, error=diagnostic)}, HTTPStatus.NOT_FOUND
        path = Path(agent.transcript).expanduser()
        try:
            generation = file_stat_signature(path)
        except OSError as exc:
            diagnostic = str(exc)
            return {"session": session, "agent": asdict(agent), **user_message_payload("transcript.error.readFailed", diagnostic, error=diagnostic)}, HTTPStatus.INTERNAL_SERVER_ERROR
        safe_messages = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        safe_lines = max(0, min(compact_lines, MAX_COMPACT_TRANSCRIPT_ITEMS))
        stable_identity = transcript_cache_identity(str(path))
        since_text = since.astimezone(timezone.utc).isoformat() if since is not None else ""
        cache_key = (stable_identity, generation, safe_messages, safe_lines, str(agent.kind or ""), since_text)
        service = self.activity_transcript_service
        with service.transcript_job_cache_lock:
            cached = service.transcript_job_cache.get(cache_key)
            job_id = service.transcript_job_records.get(cache_key, "")
        if cached is None and job_id:
            response = self.job_client.result(job_id)
            job = response.get("job") if isinstance(response.get("job"), dict) else {}
            if job.get("status") == "completed" and isinstance(job.get("result"), dict):
                result = dict(job["result"])
                expected_generation = [generation[1], generation[2]]
                if result.get("generation") == expected_generation and result.get("read_generation") == expected_generation:
                    with service.transcript_job_cache_lock:
                        self.cache_set_limited(service.transcript_job_cache, cache_key, result, CONTEXT_ITEMS_CACHE_MAX_ITEMS)
                        service.transcript_job_records.pop(cache_key, None)
                    cached = result
                else:
                    with service.transcript_job_cache_lock:
                        service.transcript_job_records.pop(cache_key, None)
            elif job.get("status") in {"failed", "cancelled", "superseded", "timed_out"}:
                with service.transcript_job_cache_lock:
                    service.transcript_job_records.pop(cache_key, None)
        if cached is None:
            generation_number = (int(generation[1]) ^ int(generation[2])) & ((1 << 63) - 1)
            request = self.job_client.submit(
                "transcript_view",
                {
                    "path": str(path.resolve(strict=False)),
                    "line_limit": MAX_TRANSCRIPT_TAIL_LINES,
                    "item_limit": safe_messages,
                    "compact_line_limit": safe_lines,
                    "kind": str(agent.kind or ""),
                    "since": since_text,
                },
                priority="freshness",
                generation=generation_number,
                coalesce_key=f"transcript:{stable_identity}:{generation[1]}:{generation[2]}:{safe_messages}:{safe_lines}:{since_text}",
                deadline_ms=15_000,
            )
            job = request.get("job") if isinstance(request.get("job"), dict) else {}
            if request.get("ok") and isinstance(job.get("job_id"), str):
                with service.transcript_job_cache_lock:
                    service.transcript_job_records[cache_key] = job["job_id"]
            return {
                "session": session,
                "path": str(path),
                "messages": safe_messages,
                "compact_lines": [],
                "items": [],
                "since_items": [],
                "since_stats": {},
                "pending": True,
                "agent": asdict(agent),
                "errors": errors,
            }, HTTPStatus.OK
        return {
            "session": session,
            "path": str(path),
            "messages": safe_messages,
            "compact_lines": list(cached.get("compact_lines") or []),
            "items": copy.deepcopy(cached.get("items") or []),
            "since_items": copy.deepcopy(cached.get("since_items") or []),
            "since_stats": dict(cached.get("since_stats") or {}),
            "pending": False,
            "agent": asdict(agent),
            "errors": errors,
        }, HTTPStatus.OK

    def context_tail(self, session: str, messages: int) -> tuple[dict[str, Any], HTTPStatus]:
        safe_messages = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        payload, status = self.transcript_compact_view(session, safe_messages, compact_lines=safe_messages)
        if status != HTTPStatus.OK:
            return payload, status
        return {
            "session": session,
            "path": payload["path"],
            "messages": safe_messages,
            "text": "\n\n".join(payload["compact_lines"]),
            "pending": bool(payload.get("pending")),
            "agent": payload.get("agent"),
            "errors": payload.get("errors", []),
        }, HTTPStatus.OK

    def context_items(self, session: str, messages: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_compact_view(session, messages)
        if status != HTTPStatus.OK:
            return payload, status
        return {
            "session": session,
            "path": payload["path"],
            "messages": payload["messages"],
            "items": payload["items"],
            "pending": bool(payload.get("pending")),
            "agent": payload.get("agent"),
            "errors": payload.get("errors", []),
        }, HTTPStatus.OK

    def codex_summary_prompt(self, session: str, lookback_seconds: int) -> tuple[dict[str, Any], HTTPStatus]:
        bounded_lookback = max(60, min(lookback_seconds, 24 * 3600))
        since = datetime.now(timezone.utc) - timedelta(seconds=bounded_lookback)
        payload, status = self.transcript_compact_view(session, MAX_COMPACT_TRANSCRIPT_ITEMS, since=since)
        if status != HTTPStatus.OK:
            return payload, status
        if payload.get("pending"):
            return {"session": session, "pending": True, "path": payload.get("path"), "agent": payload.get("agent"), "errors": payload.get("errors", [])}, HTTPStatus.ACCEPTED
        path = str(payload["path"])
        items = list(payload["since_items"])
        stats = dict(payload["since_stats"])
        fallback = False
        if not items:
            fallback = True
            items = list(payload["items"])

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
            diagnostic = cmd_error(result, "tmux next-window failed")
            return tmux_command_failure_payload(session, diagnostic), HTTPStatus.INTERNAL_SERVER_ERROR
        return {"session": session, "ok": True}, HTTPStatus.OK

    @requires_known_session()
    def tmux_status_mode(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        target = tmux_session_target(session)
        status_result = tmux(["show-options", "-A", "-t", target, "-v", "status"], timeout=3.0)
        if status_result.returncode != 0:
            diagnostic = cmd_error(status_result, "tmux status read failed")
            return tmux_command_failure_payload(session, diagnostic), HTTPStatus.INTERNAL_SERVER_ERROR
        if status_result.stdout.strip().lower() != "on":
            return {"session": session, "status": "none"}, HTTPStatus.OK
        position_result = tmux(["show-options", "-A", "-t", target, "-v", "status-position"], timeout=3.0)
        if position_result.returncode != 0:
            diagnostic = cmd_error(position_result, "tmux status position read failed")
            return tmux_command_failure_payload(session, diagnostic), HTTPStatus.INTERNAL_SERVER_ERROR
        position = position_result.stdout.strip().lower()
        return {"session": session, "status": position if position in {"top", "bottom"} else "bottom"}, HTTPStatus.OK

    @requires_known_session()
    def cycle_tmux_status_mode(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        current, status = self.tmux_status_mode(session)
        if status != HTTPStatus.OK:
            return current, status
        next_mode = {"top": "bottom", "bottom": "none", "none": "top"}[current["status"]]
        target = tmux_session_target(session)
        commands = [["set-option", "-t", target, "status", "off"]] if next_mode == "none" else [
            ["set-option", "-t", target, "status", "on"],
            ["set-option", "-t", target, "status-position", next_mode],
        ]
        for command in commands:
            result = tmux(command, timeout=3.0)
            if result.returncode != 0:
                diagnostic = cmd_error(result, "tmux status update failed")
                return tmux_command_failure_payload(session, diagnostic), HTTPStatus.INTERNAL_SERVER_ERROR
        return {"session": session, "status": next_mode}, HTTPStatus.OK

    @requires_known_session()
    def tmux_select_window(self, session: str, window: str) -> tuple[dict[str, Any], HTTPStatus]:
        window_text = str(window or "").strip()
        if not window_text.isdigit():
            diagnostic = "window must be a non-negative integer"
            return {
                "session": session,
                **user_message_payload("terminal.window.invalidNumber", diagnostic),
            }, HTTPStatus.BAD_REQUEST
        target = f"{tmux_session_target(session)}{window_text}"
        result = tmux(["select-window", "-t", target], timeout=3.0)
        if result.returncode != 0:
            diagnostic = (result.stderr or result.stdout or "tmux select-window failed").strip()
            return tmux_command_failure_payload(session, diagnostic, window=window_text), HTTPStatus.INTERNAL_SERVER_ERROR
        # select-window is the WHOLE job: it changes the session's current window for every
        # attached client synchronously (that is what a tmux session is). The retired
        # per-client `switch-client` fan-out here was a no-op by construction (it listed only
        # same-session clients, which select-window had already switched), serially delayed
        # every switch response by up to 1s per stale client, and poked the user's own
        # hand-attached terminals for nothing.
        return {"session": session, "window": window_text, "ok": True}, HTTPStatus.OK

    def stop_auto_approve_worker(self, session: str) -> None:
        approval_client = getattr(self, "approval_client", None)
        if approval_client is None:
            self.set_persisted_auto_session(session, False)
            return
        workers = approval_client.status_session(session)
        if workers:
            approval_client.stop_session(session)
        self.set_persisted_auto_session(session, False)
        if workers:
            self.commit_auto_approve_change(session, enabled=False, trigger="worker-stop")

    @requires_known_session(refresh=True)
    def rename_session(self, session: str, new_name: str) -> tuple[dict[str, Any], HTTPStatus]:
        new_name = tmux_session_name_sanitize(new_name)
        name_error = tmux_session_name_error(new_name)
        if name_error:
            error_key = {
                "session name is required": "rename.error.required",
                "session name must be 64 characters or fewer": "rename.error.tooLong",
                "session name may contain only letters, numbers, spaces, dot, dash, and underscore": "rename.error.invalidChars",
            }[name_error]
            return {
                "session": session,
                "new_name": new_name,
                **user_message_payload(error_key, name_error),
            }, HTTPStatus.BAD_REQUEST
        if new_name != session and new_name in self.sessions:
            diagnostic = f"session already exists: {new_name}"
            return {
                "session": session,
                "new_name": new_name,
                **user_message_payload("rename.error.exists", diagnostic, name=new_name),
            }, HTTPStatus.CONFLICT
        if new_name == session:
            return {"session": session, "new_session": new_name, "renamed": False, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

        result = tmux(["rename-session", "-t", tmux_session_target(session), new_name], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux rename-session failed")
            return {
                "session": session,
                "new_name": new_name,
                **user_message_payload("status.sessionRenameFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.revoke_share_tokens_for_session(session)
        self.refresh_sessions()
        self.log_event(
            new_name,
            "session_renamed",
            f"renamed {session} to {new_name}",
            {"old_session": session, "new_session": new_name},
            message_key="common.renamed",
            message_params={"oldName": session, "newName": new_name},
        )
        return {"session": session, "new_session": new_name, "renamed": True, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

    @requires_known_session(refresh=True)
    def kill_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        result = tmux(["kill-session", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux kill-session failed")
            return {
                "session": session,
                **user_message_payload("status.sessionKillFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.revoke_share_tokens_for_session(session)
        self.refresh_sessions()
        self.log_event(
            None,
            "session_killed",
            f"killed {session}",
            {"session": session},
            message_key="status.sessionKilled",
            message_params={"session": session},
        )
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
            return {
                "session": session,
                "target": target,
                "errors": errors,
                **user_message_payload("common.copyFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        if mode.stdout.strip() != "1":
            diagnostic = "tmux copy mode is not active"
            return {
                "session": session,
                "target": target,
                "copied": False,
                "text": "",
                "errors": errors,
                **user_message_payload("status.nothingSelected", diagnostic),
            }, HTTPStatus.OK

        before = tmux(["display-message", "-p", "-t", target, "#{buffer_created}:#{buffer_size}:#{buffer_sample}"], timeout=1.0)
        before_signature = before.stdout.strip() if before.returncode == 0 else ""
        copied = tmux(["send-keys", "-t", target, "-X", "copy-selection-no-clear"], timeout=1.0)
        if copied.returncode != 0:
            error = cmd_error(copied, "tmux copy selection failed")
            return {
                "session": session,
                "target": target,
                "copied": False,
                "text": "",
                "errors": errors,
                **user_message_payload("common.copyFailed", error, error=error),
            }, HTTPStatus.OK

        after = tmux(["display-message", "-p", "-t", target, "#{buffer_created}:#{buffer_size}:#{buffer_sample}"], timeout=1.0)
        if after.returncode != 0:
            error = cmd_error(after, "tmux buffer check failed")
            return {
                "session": session,
                "target": target,
                "errors": errors,
                **user_message_payload("common.copyFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        if after.stdout.strip() == before_signature:
            cancel_copy_mode_selection()
            diagnostic = "no tmux selection copied"
            return {
                "session": session,
                "target": target,
                "copied": False,
                "text": "",
                "errors": errors,
                **user_message_payload("status.nothingSelected", diagnostic),
            }, HTTPStatus.OK

        buffer_result = tmux(["save-buffer", "-"], timeout=1.0)
        if buffer_result.returncode != 0:
            cancel_copy_mode_selection()
            error = cmd_error(buffer_result, "tmux save buffer failed")
            return {
                "session": session,
                "target": target,
                "errors": errors,
                **user_message_payload("common.copyFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

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
        diagnostic = f"session no longer exists: {session}"
        return user_message_payload("status.sessionEnded", diagnostic, session=session), HTTPStatus.NOT_FOUND

    def tmux_session_exists_payload(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        clean_session = str(session or "").strip()
        if not clean_session:
            diagnostic = "session is required"
            return {"exists": False, **user_message_payload("session.error.required", diagnostic)}, HTTPStatus.BAD_REQUEST
        sessions, error = list_tmux_session_names()
        if error is not None:
            return {
                "session": clean_session,
                "exists": None,
                "diagnostic": error,
                **user_message_payload(
                    "status.sessionCheckFailed",
                    error,
                    error=message_descriptor("common.requestFailed", "request failed"),
                ),
            }, HTTPStatus.SERVICE_UNAVAILABLE
        self.sessions = sessions
        return {"session": clean_session, "exists": clean_session in sessions, "ok": True}, HTTPStatus.OK

    def create_next_session(self, agent: str, dangerously_yolo: bool | None = None, terminal: str | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        agent = agent if agent in AGENT_COMMANDS else "claude"
        available_agents = available_agent_commands()
        if agent not in available_agents:
            diagnostic = f"{agent} is not available on this server PATH"
            return {
                "agent": agent,
                "available_agents": available_agents,
                "sessions": self.sessions,
                **user_message_payload("session.error.agentUnavailablePath", diagnostic, agent=agent),
            }, HTTPStatus.NOT_FOUND
        if dangerously_yolo is True and not self.dangerously_yolo:
            diagnostic = "full-access agent launches require YOLOmux --dangerously-yolo"
            return {
                "agent": agent,
                **user_message_payload("status.sessionCreateFailedDefault", diagnostic, error=diagnostic),
            }, HTTPStatus.FORBIDDEN
        terminal_name = str(terminal or "").strip()
        if agent == "term" and not terminal_name:
            diagnostic = "choose an explicit terminal command"
            return {
                "agent": agent,
                **user_message_payload("status.sessionCreateFailedDefault", diagnostic, error=diagnostic),
            }, HTTPStatus.BAD_REQUEST
        if agent == "term" and terminal_command(terminal_name) is None:
            diagnostic = f"terminal command is not available on this server PATH: {terminal_name}"
            return {
                "agent": agent,
                "terminal": terminal_name,
                "available_terminals": available_terminal_commands(),
                **user_message_payload("session.error.agentUnavailablePath", diagnostic, agent=terminal_name),
            }, HTTPStatus.NOT_FOUND
        if len(self.sessions) >= MAX_YOLOMUX_SESSION_TABS:
            diagnostic = f"maximum session tabs reached: {MAX_YOLOMUX_SESSION_TABS}"
            return {
                "sessions": self.sessions,
                **user_message_payload("session.error.maximumTabs", diagnostic, limit=MAX_YOLOMUX_SESSION_TABS),
            }, HTTPStatus.CONFLICT
        session = next_numbered_session_name(self.sessions)
        if session is None:
            diagnostic = f"no available numbered session names from 1 to {MAX_YOLOMUX_SESSION_TABS}"
            return {
                "sessions": self.sessions,
                **user_message_payload("session.error.noAvailableNumberedNames", diagnostic, limit=MAX_YOLOMUX_SESSION_TABS),
            }, HTTPStatus.CONFLICT
        cwd = session_workdir(session)
        # An explicit launch choice is per session. Keep the server's old setting as the fallback for
        # older clients that do not send a mode, rather than silently changing their behavior.
        launch_dangerously_yolo = self.dangerously_yolo if dangerously_yolo is None else bool(dangerously_yolo)
        command = agent_command(agent, launch_dangerously_yolo, terminal=terminal_name or None)
        result = tmux(
            [
                "new-session",
                "-d",
                "-s",
                session,
                "-e",
                "TERM=xterm-256color",
                "-c",
                str(cwd),
                command,
            ],
            timeout=5.0,
        )
        if result.returncode != 0:
            error = cmd_error(result, "tmux new-session failed")
            return {
                "session": session,
                "created": False,
                **user_message_payload("status.sessionCreateFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        settings = settings_payload().get("settings", {})
        status_mode = str(settings.get("appearance", {}).get("tmux_status_bar", "off"))
        status_commands = [["set-option", "-t", tmux_session_target(session), "status", "off"]] if status_mode == "off" else [
            ["set-option", "-t", tmux_session_target(session), "status", "on"],
            ["set-option", "-t", tmux_session_target(session), "status-position", status_mode],
        ]
        for status_command in status_commands:
            tmux(status_command, timeout=3.0)
        color = self.tmux_theme_color or tmux_theme_color_from_settings(settings)
        theme_result = apply_tmux_theme_color_to_new_session(session, color, runner=tmux)
        self.tmux_theme_color = color
        if theme_result.get("errors"):
            logger.debug("tmux theme apply failed for new session %s: %s", session, theme_result.get("errors"))
        self.refresh_sessions()
        self.log_event(
            session,
            "session_started",
            f"created {session} with {agent}",
            {"agent": agent, "cwd": str(cwd), "command": command, "dangerously_yolo": launch_dangerously_yolo, "terminal": terminal_name},
            message_key="status.sessionCreatedWithAgent",
            message_params={"session": session, "agent": agent},
        )
        return {
            "session": session,
            "sessions": self.sessions,
            "agent": agent,
            "created": True,
            "cwd": str(cwd),
            "command": command,
            "dangerously_yolo": launch_dangerously_yolo,
            "terminal": terminal_name,
            "ok": True,
        }, HTTPStatus.OK

    def _save_uploaded_files(self, target_dir: Path, files: list[UploadedFile]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, HTTPStatus]:
        saved: list[dict[str, Any]] = []
        upload_template = settings_payload().get("settings", {}).get("uploads", {}).get("filename_template")
        for upload in files:
            safe_name = sanitize_upload_filename(upload.filename)
            path: Path | None = None
            last_error: OSError | None = None
            for _attempt in range(1000):
                candidate: Path | None = None
                try:
                    candidate = unique_upload_path(target_dir, safe_name, str(upload_template or ""))
                    with candidate.open("xb") as stream:
                        stream.write(upload.content)
                    candidate.chmod(0o600)
                    path = candidate
                    break
                except FileExistsError as exc:
                    last_error = exc
                    continue
                except OSError as exc:
                    last_error = exc
                    try:
                        if candidate is not None:
                            candidate.unlink(missing_ok=True)
                    except OSError:
                        pass
                    break
            if path is None:
                exc = last_error or OSError("failed to reserve a unique upload filename")
                diagnostic = f"failed to save {safe_name}: {exc}"
                return [], {
                    "target_dir": str(target_dir),
                    **user_message_payload("status.uploadFailed", diagnostic, error=diagnostic),
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
    def upload_files(self, session: str, files: list[UploadedFile], *, auth_username: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        if not files:
            diagnostic = "no files supplied"
            return {
                "session": session,
                **user_message_payload("upload.error.noFiles", diagnostic),
            }, HTTPStatus.BAD_REQUEST
        if len(files) > UPLOAD_MAX_FILES:
            diagnostic = f"too many files; limit is {UPLOAD_MAX_FILES}"
            return {
                "session": session,
                **user_message_payload("upload.error.tooManyFiles", diagnostic, limit=UPLOAD_MAX_FILES),
            }, HTTPStatus.REQUEST_ENTITY_TOO_LARGE

        try:
            target_dir, target_source = self.upload_target_dir(session, auth_username=auth_username)
        except UploadTargetError as exc:
            diagnostic = str(exc)
            return {
                "session": session,
                **user_message_payload("status.uploadFailed", diagnostic, error=diagnostic),
            }, HTTPStatus.CONFLICT

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
            message_key="events.message.upload.files",
            message_params={"count": len(saved)},
        )
        return {
            "session": session,
            "target_dir": str(target_dir),
            "target_source": target_source,
            "files": saved,
        }, HTTPStatus.OK

    def upload_editor_files(
        self,
        files: list[UploadedFile],
        *,
        editor_path: str = "",
        base_dir: str = "",
        auth_username: str = "",
        session: str = "editor",
    ) -> tuple[dict[str, Any], HTTPStatus]:
        if not files:
            diagnostic = "no files supplied"
            return user_message_payload("upload.error.noFiles", diagnostic), HTTPStatus.BAD_REQUEST
        if len(files) > UPLOAD_MAX_FILES:
            diagnostic = f"too many files; limit is {UPLOAD_MAX_FILES}"
            return user_message_payload("upload.error.tooManyFiles", diagnostic, limit=UPLOAD_MAX_FILES), HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        raw_base = str(base_dir or "").strip()
        raw_editor_path = str(editor_path or "").strip()
        if not raw_base and not raw_editor_path:
            diagnostic = "missing editor_path or base_dir"
            return user_message_payload("upload.error.editorTargetRequired", diagnostic), HTTPStatus.BAD_REQUEST
        base = Path(raw_base).expanduser() if raw_base else Path(raw_editor_path).expanduser().parent
        try:
            target_dir, target_source = self.upload_target_dir(session or "editor", auth_username=auth_username)
        except UploadTargetError as exc:
            diagnostic = str(exc)
            return {
                "base_dir": str(base),
                **user_message_payload("status.uploadFailed", diagnostic, error=diagnostic),
            }, HTTPStatus.CONFLICT
        saved, error, status = self._save_uploaded_files(target_dir, files)
        if error is not None:
            error["base_dir"] = str(base)
            return error, status
        for item in saved:
            item["relative_path"] = item["path"]
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
            message_key="events.message.upload.editorFiles",
            message_params={"count": len(saved)},
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

    def file_transfer_max_bytes(self) -> int:
        value = settings_payload().get("settings", {}).get("uploads", {}).get("max_bytes", UPLOAD_MAX_BYTES)
        return int(value) if isinstance(value, (int, float)) and value > 0 else UPLOAD_MAX_BYTES

    def upload_max_bytes(self) -> int:
        return self.file_transfer_max_bytes()

    def upload_target_dir(self, session: str, *, auth_username: str = "") -> tuple[Path, str]:
        target, user_root = central_upload_target(auth_username, session)
        retention_days = settings_payload().get("settings", {}).get("uploads", {}).get("retention_days", 7)
        try:
            self.upload_retention_sweeper.maybe_prune(user_root, int(retention_days))
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("upload retention sweep failed for %s: %s", user_root, exc)
        return target, "central_user_uploads"

    @requires_known_session()
    def set_auto_approve(self, session: str, enabled: bool, persist: bool = True, takeover: bool = True) -> tuple[AutoApproveState, HTTPStatus]:
        changed = False
        if enabled:
            if not tmux_has_exact_session(session):
                diagnostic = f"tmux session not found: {session}"
                return {
                    "session": session,
                    "enabled": False,
                    **user_message_payload("status.sessionEnded", diagnostic, session=session),
                }, HTTPStatus.NOT_FOUND
            started, status = self.ensure_auto_approve_agent_workers(session, takeover=takeover)
            if not started:
                return status, HTTPStatus.CONFLICT
            if persist:
                self.set_persisted_auto_session(session, True)
            changed = True
            self.log_event(
                session,
                "yolo_enabled",
                "YOLO enabled",
                {"persist": persist},
                message_key="events.message.yolo.enabled",
            )
            return self.auto_approve_session_status(session), HTTPStatus.OK

        records = self.approval_client.status_session(session)
        response = self.approval_client.stop_session(session) if records else {"ok": True}
        if records and response.get("ok"):
            if persist:
                self.set_persisted_auto_session(session, False)
            changed = True
            self.log_event(
                session,
                "yolo_disabled",
                "YOLO disabled",
                {"persist": persist},
                message_key="events.message.yolo.disabled",
            )
        status_payload = self.auto_approve_session_status(session)
        if changed:
            self.commit_auto_approve_change(session, enabled=bool(status_payload.get("enabled")), trigger="set-auto-approve")
        return status_payload, HTTPStatus.OK

    def commit_auto_approve_change(self, session: str, *, enabled: bool, trigger: str) -> None:
        """Commit the visibility side of an already-applied YOLO worker-state mutation."""
        self.invalidate_auto_approve_cache()
        self.publish_background_client_event(
            "auto_approve_changed",
            {"session": session, "enabled": enabled},
            trigger=trigger,
            cache="ready",
        )

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

    def ensure_auto_approve_agent_workers(self, session: str, takeover: bool) -> tuple[bool, AutoApproveState]:
        desired_targets = self.auto_approve_agent_targets(session) or [session]
        desired = set(desired_targets)
        existing_statuses = self.approval_client.status_session(session)
        for status in existing_statuses:
            key = str(status.get("target") or "")
            if key and key not in desired:
                self.approval_client.stop_target(key)
        first_error: AutoApproveState | None = None
        started_any = False
        for target in desired_targets:
            existing = next((status for status in existing_statuses if status.get("target") == target and status.get("enabled") is True), None)
            if existing is not None:
                started_any = True
                continue
            started, status = self.start_auto_approve_worker(session, takeover=takeover, target=target)
            if not started:
                if first_error is None:
                    first_error = status
                continue
            started_any = True
        if started_any:
            return True, {"session": session, "target": session, "enabled": True}
        return False, first_error or {"session": session, "enabled": False, "error": "failed to start YOLO worker"}

    def sync_auto_approve_agent_workers(self, takeover: bool = False) -> None:
        for session in self.persisted_auto_sessions():
            if session in self.sessions:
                self.ensure_auto_approve_agent_workers(session, takeover=takeover)

    def start_auto_approve_worker(self, session: str, takeover: bool, target: str | None = None) -> tuple[object | None, AutoApproveState]:
        worker_target = str(target or session)
        owner_extra = self.control_server.owner_payload()
        owner_extra["session"] = session
        worker, status = self.approval_client.start_worker(
            session=session,
            target=worker_target,
            owner_extra=owner_extra,
            dangerously_yolo=self.dangerously_yolo,
        )
        if worker is not None:
            status["session"] = session
            return worker, status
        owner = status.get("lock_owner") if isinstance(status.get("lock_owner"), dict) else None
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
                worker, retry_status = self.approval_client.start_worker(
                    session=session,
                    target=worker_target,
                    owner_extra=owner_extra,
                    dangerously_yolo=self.dangerously_yolo,
                )
                if worker is not None:
                    self.log_event(
                        session,
                        "yolo_takeover",
                        "YOLO moved from another server",
                        {"owner": locked_owner or {}},
                        message_key="events.message.yolo.takeover",
                    )
                    status = retry_status
                    status["session"] = session
                    return worker, status
                owner = retry_status.get("lock_owner") if isinstance(retry_status.get("lock_owner"), dict) else None
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        payload: AutoApproveState = dict(status)
        payload.update({
            "session": session,
            "enabled": False,
            "enabled_elsewhere": True,
            "locked": True,
            "lock_owner": owner,
            "error": auto_approve_lock_message(owner),
        })
        self.log_event(
            session,
            "yolo_locked",
            "YOLO already owned by another server",
            {"owner": owner or {}},
            message_key="events.message.yolo.locked",
        )
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
            self.log_event(
                session,
                "yolo_takeover_failed",
                "YOLO owner did not release",
                {"owner": owner or {}, "response": response},
                message_key="events.message.yolo.takeoverFailed",
            )
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
        targets = [str(status.get("target") or "") for status in self.approval_client.status_session(session)]
        return any(target and self.approval_client.has_pending_prompt(target) for target in targets)

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

    def agent_window_working_stopped_ts(
        self,
        session: str,
        window: str,
        pane_target: str,
        kind: str,
        state: str,
        observed_ts: float,
        shared_instances: dict[str, AgentWindowAttentionInstance] | None = None,
        return_pending: bool = False,
    ) -> float | tuple[float, bool]:
        key = "\x1f".join((session, window, pane_target, kind))
        with self.agent_window_transition_lock:
            previous = self.agent_window_transition_state.get(key, {})
            previous_state = str(previous.get("state") or "")
            previous_stopped_ts = self.float_value(previous.get("working_stopped_ts"), 0.0)
            try:
                previous_generation = max(0, int(previous.get("cooldown_generation", 0)))
            except (TypeError, ValueError):
                previous_generation = 0
            # A process can begin watching after another YOLOmux server observed the
            # working->idle transition. Hydrate that durable identity before using the
            # local shadow state, otherwise this follower renders ordinary idle while
            # the owner correctly renders the shared yellow completion.
            shared_generation, shared_stopped_ts, shared_idle_since = self.shared_agent_window_cooldown_state(
                session,
                window,
                pane_target,
                kind,
                shared_instances=shared_instances,
            )
            if shared_generation >= previous_generation:
                previous_generation = shared_generation
                previous_stopped_ts = shared_stopped_ts
            pending_idle_since = shared_idle_since if shared_generation >= previous_generation else 0.0
            generation = previous_generation
            if state == "working":
                stopped_ts = 0.0
                generation, _stopped_ts, pending_idle_since = self.shared_agent_window_cooldown_transition(
                    session,
                    window,
                    pane_target,
                    kind,
                    "working",
                    previous_generation,
                    observed_ts,
                )
                if generation > 0:
                    self.update_shared_agent_window_instance_snapshot(
                        shared_instances,
                        session,
                        window,
                        pane_target,
                        kind,
                        cooldown_generation=generation,
                        cooldown_stopped_at=0.0,
                        cooldown_idle_since=pending_idle_since,
                        cooldown_working=True,
                    )
            elif state == "idle":
                # A completion belongs only to a working->idle transition observed by this
                # tracker. Activity recency is historical metadata: treating it as a stop
                # fabricates a yellow completion when a renamed or newly discovered session is
                # first seen idle.
                if previous_generation > 0:
                    generation, stopped_ts, pending_idle_since = self.shared_agent_window_cooldown_transition(
                        session,
                        window,
                        pane_target,
                        kind,
                        "idle-pending",
                        previous_generation,
                        observed_ts,
                    )
                    if generation > 0 and pending_idle_since > 0 and observed_ts - pending_idle_since >= AGENT_WORKING_IDLE_CONFIRM_SECONDS:
                        generation, stopped_ts, pending_idle_since = self.shared_agent_window_cooldown_transition(
                            session,
                            window,
                            pane_target,
                            kind,
                            "idle",
                            generation,
                            observed_ts,
                        )
                    if generation > 0:
                        self.update_shared_agent_window_instance_snapshot(
                            shared_instances,
                            session,
                            window,
                            pane_target,
                            kind,
                            cooldown_generation=generation,
                            cooldown_stopped_at=stopped_ts,
                            cooldown_idle_since=pending_idle_since,
                            cooldown_working=stopped_ts <= 0,
                        )
                else:
                    stopped_ts = previous_stopped_ts
                    pending_idle_since = 0.0
            else:
                stopped_ts = 0.0
                pending_idle_since = 0.0
                if previous_generation > 0:
                    self.shared_agent_window_cooldown_transition(
                        session,
                        window,
                        pane_target,
                        kind,
                        "cancel",
                        previous_generation,
                        observed_ts,
                    )
                generation = 0
            # Keep the prompt-transition fields alongside the working transition. A later
            # approval uses them to distinguish A -> B -> A from one still-visible A prompt.
            next_state = dict(previous)
            next_state.update({"state": state, "working_stopped_ts": stopped_ts, "cooldown_generation": generation, "cooldown_idle_since": pending_idle_since})
            self.agent_window_transition_state[key] = next_state
        return (stopped_ts, pending_idle_since > 0 and stopped_ts <= 0) if return_pending else stopped_ts

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
        # The visible question is often the same for every Claude approval (for example, “Do you
        # want to proceed?”). Prefer its prompt hash; the caller adds a per-window generation.
        for key in ("prompt_hash", "signature", "hash", "question_text", "text", "key"):
            value = str(screen_payload.get(key) or "").strip()
            if value:
                return value
        return state

    @staticmethod
    def agent_window_attention_instance_key(session: str, window: str, pane_target: str, kind: str) -> str:
        return "\x1f".join((session, window, pane_target, kind))

    @staticmethod
    def prune_attention_instances(instances: dict[str, dict[str, Any]], now: float) -> None:
        for key, record in list(instances.items()):
            try:
                updated_at = float(record.get("updated_at") or 0.0)
            except (AttributeError, TypeError, ValueError):
                updated_at = 0.0
            if not isinstance(record, dict) or now - updated_at > ATTENTION_ACK_TTL_SECONDS:
                instances.pop(key, None)
        while len(instances) > ATTENTION_INSTANCE_MAX_ENTRIES:
            oldest = min(instances, key=lambda item: float(instances[item].get("updated_at") or 0.0))
            instances.pop(oldest, None)

    def update_shared_agent_window_attention_instance(
        self,
        session: str,
        window: str,
        pane_target: str,
        kind: str,
        update: Callable[[dict[str, Any], float], tuple[Any, bool]],
    ) -> Any:
        key = self.agent_window_attention_instance_key(session, window, pane_target, kind)
        now = time.time()
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            container = status.get("attention_instances") if isinstance(status.get("attention_instances"), dict) else {}
            instances = container.get("instances") if isinstance(container.get("instances"), dict) else {}
            instances = {str(instance_key): dict(record) for instance_key, record in instances.items() if isinstance(record, dict)}
            self.prune_attention_instances(instances, now)
            record = dict(instances.get(key, {}))
            result, changed = update(record, now)
            if changed:
                record["updated_at"] = now
                instances[key] = record
                status["attention_instances"] = {"updated_at": now, "instances": instances}
                self._write_shared_tmux_ai_status_locked(status)
        return result

    def shared_agent_window_attention_instances_snapshot(self) -> dict[str, AgentWindowAttentionInstance]:
        now = time.time()
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            container = status.get("attention_instances") if isinstance(status.get("attention_instances"), dict) else {}
            raw_instances = container.get("instances") if isinstance(container.get("instances"), dict) else {}
            instances = {str(key): dict(value) for key, value in raw_instances.items() if isinstance(value, dict)}
            self.prune_attention_instances(instances, now)
        return {key: AgentWindowAttentionInstance.from_record(record) for key, record in instances.items()}

    def update_shared_agent_window_instance_snapshot(
        self,
        shared_instances: dict[str, AgentWindowAttentionInstance] | None,
        session: str,
        window: str,
        pane_target: str,
        kind: str,
        **changes: Any,
    ) -> None:
        if shared_instances is None:
            return
        key = self.agent_window_attention_instance_key(session, window, pane_target, kind)
        current = shared_instances.get(key, AgentWindowAttentionInstance())
        shared_instances[key] = AgentWindowAttentionInstance(
            cooldown_generation=int(changes.get("cooldown_generation", current.cooldown_generation)),
            cooldown_stopped_at=float(changes.get("cooldown_stopped_at", current.cooldown_stopped_at)),
            cooldown_idle_since=float(changes.get("cooldown_idle_since", current.cooldown_idle_since)),
            cooldown_cancelled_generation=int(changes.get("cooldown_cancelled_generation", current.cooldown_cancelled_generation)),
            cooldown_working=bool(changes.get("cooldown_working", current.cooldown_working)),
            attention_generation=int(changes.get("attention_generation", current.attention_generation)),
            active_prompt_hash=str(changes.get("active_prompt_hash", current.active_prompt_hash)),
        )

    def shared_agent_window_cooldown_state(
        self,
        session: str,
        window: str,
        pane_target: str,
        kind: str,
        shared_instances: dict[str, AgentWindowAttentionInstance] | None = None,
    ) -> tuple[int, float, float]:
        """Read the durable completion identity used by every server process."""

        key = self.agent_window_attention_instance_key(session, window, pane_target, kind)
        if shared_instances is not None:
            instance = shared_instances.get(key, AgentWindowAttentionInstance())
            generation, stopped_at = instance.cooldown_state()
            return generation, stopped_at, instance.cooldown_idle_since if stopped_at <= 0 else 0.0

        def read(record: dict[str, Any], _now: float) -> tuple[tuple[int, float, float], bool]:
            instance = AgentWindowAttentionInstance.from_record(record)
            generation, stopped_at = instance.cooldown_state()
            return (generation, stopped_at, instance.cooldown_idle_since if stopped_at <= 0 else 0.0), False

        return self.update_shared_agent_window_attention_instance(session, window, pane_target, kind, read)

    def shared_agent_window_cooldown_transition(
        self,
        session: str,
        window: str,
        pane_target: str,
        kind: str,
        transition: str,
        local_generation: int,
        observed_ts: float,
    ) -> tuple[int, float, float]:
        def update(record: dict[str, Any], now: float) -> tuple[tuple[int, float, float], bool]:
            instance = AgentWindowAttentionInstance.from_record(record)
            generation = instance.cooldown_generation
            stopped_ts = instance.cooldown_stopped_at
            idle_since = instance.cooldown_idle_since
            cancelled_generation = instance.cooldown_cancelled_generation
            working = instance.cooldown_working
            if transition == "working":
                if not working:
                    generation += 1
                    record.update({"cooldown_generation": generation, "cooldown_working": True, "cooldown_stopped_at": 0.0, "cooldown_idle_since": 0.0})
                    return (generation, 0.0, 0.0), True
                if idle_since > 0:
                    record["cooldown_idle_since"] = 0.0
                    return (generation, 0.0, 0.0), True
                return (generation, 0.0, 0.0), False
            if local_generation <= 0 or local_generation != generation:
                return (0, 0.0, 0.0), False
            if transition == "idle-pending":
                if cancelled_generation >= generation or stopped_ts > 0:
                    return (generation, stopped_ts, 0.0), False
                if idle_since <= 0:
                    idle_since = observed_ts if observed_ts > 0 else now
                    record["cooldown_idle_since"] = idle_since
                    return (generation, 0.0, idle_since), True
                return (generation, 0.0, idle_since), False
            if transition == "idle":
                if cancelled_generation >= generation:
                    return (0, 0.0, 0.0), False
                if idle_since <= 0 or (observed_ts if observed_ts > 0 else now) - idle_since < AGENT_WORKING_IDLE_CONFIRM_SECONDS:
                    return (generation, 0.0, idle_since), False
                changed = False
                if stopped_ts <= 0:
                    stopped_ts = idle_since
                    record["cooldown_stopped_at"] = stopped_ts
                    record["cooldown_idle_since"] = 0.0
                    changed = True
                if working:
                    record["cooldown_working"] = False
                    changed = True
                return (generation, stopped_ts, 0.0), changed
            if transition == "cancel" and stopped_ts <= 0 and (working or cancelled_generation < generation):
                record.update({"cooldown_working": False, "cooldown_idle_since": 0.0, "cooldown_cancelled_generation": generation})
                return (generation, 0.0, 0.0), True
            return (generation, 0.0, idle_since if stopped_ts <= 0 else 0.0), False

        return self.update_shared_agent_window_attention_instance(session, window, pane_target, kind, update)

    def shared_agent_window_attention_instance_signature(
        self,
        session: str,
        window: str,
        pane_target: str,
        kind: str,
        state: str,
        prompt_hash: str,
        shared_instances: dict[str, AgentWindowAttentionInstance] | None = None,
    ) -> str:
        attention_state = state in {"approval", "needs-approval", "needs-input", "interrupted"}
        key = self.agent_window_attention_instance_key(session, window, pane_target, kind)
        snapshot = shared_instances.get(key, AgentWindowAttentionInstance()) if shared_instances is not None else None
        if snapshot is not None:
            if not attention_state or not prompt_hash:
                if not snapshot.active_prompt_hash:
                    return ""
            elif prompt_hash == snapshot.active_prompt_hash:
                return f"{prompt_hash}:{snapshot.attention_generation}"

        def update(record: dict[str, Any], _now: float) -> tuple[str, bool]:
            instance = AgentWindowAttentionInstance.from_record(record)
            previous_hash = instance.active_prompt_hash
            generation = instance.attention_generation
            if not attention_state or not prompt_hash:
                if previous_hash:
                    record["active_prompt_hash"] = ""
                    return "", True
                return "", False
            if prompt_hash != previous_hash:
                generation += 1
                record["active_prompt_hash"] = prompt_hash
                record["attention_generation"] = generation
                return f"{prompt_hash}:{generation}", True
            return f"{prompt_hash}:{generation}", False

        signature = self.update_shared_agent_window_attention_instance(session, window, pane_target, kind, update)
        if shared_instances is not None:
            if not attention_state or not prompt_hash:
                self.update_shared_agent_window_instance_snapshot(
                    shared_instances,
                    session,
                    window,
                    pane_target,
                    kind,
                    active_prompt_hash="",
                )
            else:
                try:
                    generation = max(0, int(str(signature).rsplit(":", 1)[1]))
                except (IndexError, ValueError):
                    generation = 0
                self.update_shared_agent_window_instance_snapshot(
                    shared_instances,
                    session,
                    window,
                    pane_target,
                    kind,
                    active_prompt_hash=prompt_hash,
                    attention_generation=generation,
                )
        return signature

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

    def attention_acknowledged_at(self, key: str) -> float | None:
        if not key:
            return None
        with self.attention_ack_lock:
            self.prune_attention_ack_keys_locked()
            try:
                acknowledged_at = float(self.attention_ack_keys.get(key) or 0.0)
            except (TypeError, ValueError):
                return None
        return acknowledged_at if acknowledged_at > 0 else None

    def invalidate_auto_approve_cache(self) -> None:
        with self.auto_approve_cache_condition:
            record = self.auto_approve_cache_record
            record.payload = None
            record.worker = None
            record.generation += 1
            self.auto_approve_cache_condition.notify_all()
        self.status_client.invalidate("auto_approve")

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

    def write_shared_attention_acks_union(self, local_keys: dict[str, float]) -> tuple[int, list[str]]:
        now = time.time()
        with file_lock(common.TMUX_AI_STATUS_PATH, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            attention = status.get("attention_acks") if isinstance(status.get("attention_acks"), dict) else {}
            merged, rev = self._read_shared_attention_acks_locked()
            self._prune_attention_ack_dict(merged, now)
            before_keys = set(merged)
            for key, ts in local_keys.items():
                if key and ts > 0 and key not in merged:
                    merged[key] = ts
            self._prune_attention_ack_dict(merged, now)
            newly_acknowledged = sorted(set(merged) - before_keys)
            if newly_acknowledged:
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
        with self.client_watch_service.lock:
            self.client_watch_service.attention_ack_rev = rev
        return rev, newly_acknowledged

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
            with self.client_watch_service.lock:
                if rev <= self.client_watch_service.attention_ack_rev:
                    return False
                self.client_watch_service.attention_ack_rev = rev
            with self.attention_ack_lock:
                changed = set(self.attention_ack_keys) != set(file_keys)
                self.attention_ack_keys = dict(file_keys)
        return changed

    def poll_attention_acks_client_event_once(self) -> list[str]:
        with self.attention_ack_lock:
            previous_keys = set(self.attention_ack_keys)
        if not self.merge_shared_attention_acks():
            return []
        self.invalidate_auto_approve_cache()
        with self.attention_ack_lock:
            acknowledged = sorted(set(self.attention_ack_keys) - previous_keys)
            acknowledged_at = {key: self.attention_ack_keys[key] for key in acknowledged}
        self.publish_client_event(
            "attention_acks_changed",
            {"acknowledged": acknowledged, "acknowledged_at": acknowledged_at},
            trigger="timer",
            cache="ready",
        )
        return ["attention_acks_changed"]

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
            return user_message_payload("common.requestFailed", "attention acknowledgement keys required"), HTTPStatus.BAD_REQUEST
        now = time.time()
        rev, newly_acknowledged = self.write_shared_attention_acks_union({key: now for key in keys})
        with self.attention_ack_lock:
            acknowledged_at = {key: self.attention_ack_keys[key] for key in keys if key in self.attention_ack_keys}
        result = {
            "ok": True,
            "acknowledged": keys,
            "acknowledged_at": acknowledged_at,
            "changed": bool(newly_acknowledged),
            "rev": rev,
            "status": int(HTTPStatus.OK),
        }
        if not newly_acknowledged:
            return result, HTTPStatus.OK
        event_payload = {
            "acknowledged": newly_acknowledged,
            "acknowledged_at": {key: acknowledged_at[key] for key in newly_acknowledged},
        }
        self.notify_background_client_event_followers(
            "attention_acks_changed",
            event_payload,
            self.shared_background_client_event_record("attention_acks_changed", event_payload),
        )
        self.invalidate_auto_approve_cache()
        self.publish_client_event("attention_acks_changed", event_payload, trigger="attention_ack", cache="ready")
        return result, HTTPStatus.OK

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
    def agent_window_pane_maps(info: SessionInfo) -> tuple[dict[str, bool], dict[str, TmuxPaneInfo]]:
        current_by_window: dict[str, bool] = {}
        pane_by_window: dict[str, TmuxPaneInfo] = {}
        for pane in info.panes:
            window = TmuxWebtermApp.agent_window_index_key(pane.window)
            if not window:
                continue
            current_by_window[window] = current_by_window.get(window, False) or pane.window_active is True
            current = pane_by_window.get(window)
            if current is None or (pane.active and not current.active) or (pane.window_active and not current.window_active):
                pane_by_window[window] = pane
        return current_by_window, pane_by_window

    def agent_window_fallback_path_record(self, pane: TmuxPaneInfo | None, git_cache: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
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
        owned_rows_by_target: dict[tuple[str, str, str], dict[str, Any]] | None = None,
        snapshot_revision: int = 0,
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
        shared_instances = self.shared_agent_window_attention_instances_snapshot()
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
            working_stopped_ts = self.agent_window_working_stopped_ts(
                session,
                window,
                str(agent.pane_target or ""),
                kind,
                state,
                observed_ts,
                shared_instances=shared_instances,
            )
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
            prompt_hash = self.agent_window_attention_signature(state, screen)
            attention_signature = self.shared_agent_window_attention_instance_signature(
                session,
                window,
                str(agent.pane_target or ""),
                kind,
                state,
                prompt_hash,
                shared_instances=shared_instances,
            )
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
                row["attention_acknowledged_at"] = self.attention_acknowledged_at(attention_key)
            if cooldown_attention_key:
                row["cooldown_attention_key"] = cooldown_attention_key
                row["cooldown_acknowledged"] = self.attention_acknowledged(cooldown_attention_key)
                row["cooldown_acknowledged_at"] = self.attention_acknowledged_at(cooldown_attention_key)
            owned = (owned_rows_by_target or {}).get((session, str(agent.pane_target or ""), kind))
            if owned is not None:
                # Keep locally computed path metadata, but never publish a second prompt/screen
                # classification while a roster revision is available.
                for field_name in (
                    "state", "working_elapsed_seconds", "idle_since", "last_active_ts",
                    "working_stopped_ts", "observed_ts", "screen_text", "status_tokens",
                    "attention_key", "attention_acknowledged", "attention_acknowledged_at",
                    "cooldown_attention_key", "cooldown_acknowledged", "cooldown_acknowledged_at",
                ):
                    if field_name in owned:
                        row[field_name] = copy.deepcopy(owned[field_name])
                    else:
                        row.pop(field_name, None)
                row["agent_window_snapshot_revision"] = snapshot_revision
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
        statuses = self.approval_client.status_session(session)
        if statuses:
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
                **message_fields("last_action", "state.off", "off"),
            }
            owner = self.auto_approve_session_lock_owner(session, discovered_sessions=discovered_sessions)
            if owner:
                payload.update({
                    "enabled_elsewhere": True,
                    "locked": True,
                    "lock_owner": owner,
                    "error": auto_approve_lock_message(owner),
                    **auto_approve_lock_message_fields("last_action", owner),
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
        # Each prompt/window row below is the authority for its current key.
        # Do not attach the historical seven-day ledger here: all clients get
        # acknowledgement deltas through attention_acks_changed, and a compact
        # revision lets an explicit refresh reconcile ownership.
        with self.client_watch_service.lock:
            payload["attention_ack_revision"] = self.client_watch_service.attention_ack_rev
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
        *,
        sync_workers: bool = True,
    ) -> tuple[AutoApproveState | AutoApproveStatusPayload, HTTPStatus]:
        refresh_started = time.perf_counter()
        refresh_errors = self.refresh_sessions(maintenance=False)
        add_phase_timing(timings, "refresh_sessions", refresh_started)
        if session is not None and session not in self.sessions:
            diagnostic = f"unknown session: {session}"
            return user_message_payload("yoagent.error.unknownSession", diagnostic, session=session), HTTPStatus.NOT_FOUND
        removed = False
        if sync_workers:
            worker_started = time.perf_counter()
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
        discovery = discover_status_sessions if self.status_service_mode else discover_sessions
        discovered_sessions, discovery_errors = discovery(self.sessions)
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
        with self.client_watch_service.lock:
            payload["attention_ack_revision"] = self.client_watch_service.attention_ack_rev
        if timings:
            payload["timings"] = dict(timings)
        return payload, HTTPStatus.OK

    def auto_approve_cache_payload(self, cached: tuple[float, tuple[AutoApproveStatusPayload, HTTPStatus]]) -> tuple[AutoApproveStatusPayload, HTTPStatus]:
        _, (payload, status) = cached
        age_seconds = self.auto_approve_cache_age_seconds(cached)
        result = copy.deepcopy(payload)
        result["cache"] = {
            "age_seconds": round(age_seconds, 3),
            "max_age_seconds": AUTO_APPROVE_CACHE_MAX_AGE_SECONDS,
            "refreshing": self.auto_approve_cache_record.worker is not None,
            "stale": age_seconds > AUTO_APPROVE_CACHE_MAX_AGE_SECONDS,
        }
        return result, status

    def auto_approve_cache_age_seconds(self, cached: tuple[float, tuple[AutoApproveStatusPayload, HTTPStatus]]) -> float:
        return max(0.0, time.monotonic() - cached[0])

    def auto_approve_cache_is_fresh(self, cached: tuple[float, tuple[AutoApproveStatusPayload, HTTPStatus]]) -> bool:
        return self.auto_approve_cache_age_seconds(cached) <= AUTO_APPROVE_CACHE_MAX_AGE_SECONDS

    def set_auto_approve_cache(self, payload: AutoApproveStatusPayload, status: HTTPStatus, generation: int, worker: object) -> bool:
        with self.auto_approve_cache_condition:
            record = self.auto_approve_cache_record
            if record.generation != generation or record.worker is not worker:
                return False
            cached_payload = copy.deepcopy(payload)
            if status == HTTPStatus.OK:
                record.agent_window_snapshot_revision += 1
                cached_payload["agent_window_snapshot_revision"] = record.agent_window_snapshot_revision
            record.payload = (time.monotonic(), (cached_payload, status))
            record.worker = None
            self.auto_approve_cache_condition.notify_all()
            return True

    def finish_auto_approve_cache_refresh(self, generation: int, worker: object) -> bool:
        with self.auto_approve_cache_condition:
            record = self.auto_approve_cache_record
            if record.generation != generation or record.worker is not worker:
                return False
            record.worker = None
            self.auto_approve_cache_condition.notify_all()
            return True

    def run_auto_approve_cache_refresh(self, generation: int, worker: object) -> None:
        try:
            timings: dict[str, float] = {}
            payload, status = self.build_auto_approve_status(timings=timings)
            if isinstance(payload, dict):
                payload["timings"] = dict(timings)
            self.set_auto_approve_cache(payload, status, generation, worker)
        except Exception:
            logger.exception("auto-approve cache refresh failed")
            self.finish_auto_approve_cache_refresh(generation, worker)

    def start_auto_approve_cache_refresh(self) -> bool:
        with self.auto_approve_cache_condition:
            record = self.auto_approve_cache_record
            if record.worker is not None:
                return False
            record.generation += 1
            generation = record.generation
            worker = object()
            record.worker = worker
        thread = threading.Thread(target=self.run_auto_approve_cache_refresh, args=(generation, worker), name="auto-approve-cache-refresh", daemon=True)
        try:
            thread.start()
        except Exception:
            self.finish_auto_approve_cache_refresh(generation, worker)
            raise
        return True

    def refresh_auto_approve_cache_sync(self, require_fresh: bool = False) -> tuple[AutoApproveStatusPayload, HTTPStatus]:
        while True:
            with self.auto_approve_cache_condition:
                record = self.auto_approve_cache_record
                while record.worker is not None and (record.payload is None or (require_fresh and not self.auto_approve_cache_is_fresh(record.payload))):
                    self.auto_approve_cache_condition.wait(timeout=0.5)
                if record.payload is not None and (not require_fresh or self.auto_approve_cache_is_fresh(record.payload)):
                    return self.auto_approve_cache_payload(record.payload)
                record.generation += 1
                generation = record.generation
                worker = object()
                record.worker = worker
            try:
                timings: dict[str, float] = {}
                payload, status = self.build_auto_approve_status(timings=timings)
                if isinstance(payload, dict):
                    payload["timings"] = dict(timings)
                if not self.set_auto_approve_cache(payload, status, generation, worker):
                    continue
                with self.auto_approve_cache_condition:
                    cached = self.auto_approve_cache_record.payload
                    assert cached is not None
                    return self.auto_approve_cache_payload(cached)
            except Exception:
                self.finish_auto_approve_cache_refresh(generation, worker)
                raise

    def auto_approve_status(self, session: str | None = None) -> tuple[AutoApproveState | AutoApproveStatusPayload, HTTPStatus]:
        # Cross-process push and SSE polling are latency optimizations, not correctness boundaries.
        # A peer can miss both while disconnected, so every explicit status read must first observe
        # the shared acknowledgement revision and discard a cache built before that revision.
        if self.merge_shared_attention_acks():
            self.invalidate_auto_approve_cache()
        if session is not None:
            timings: dict[str, float] = {}
            return self.build_auto_approve_status(session, timings=timings)
        with self.auto_approve_cache_condition:
            cached = self.auto_approve_cache_record.payload
            if cached is not None:
                if not self.auto_approve_cache_is_fresh(cached):
                    self.start_auto_approve_cache_refresh()
                return self.auto_approve_cache_payload(cached)
        return self.refresh_auto_approve_cache_sync()

    def auto_approve_status_bytes(self, session: str | None = None) -> tuple[bytes, HTTPStatus]:
        """Return daemon-owned status bytes without web-side discovery or encoding."""

        response, body = self.status_client.snapshot(self.sessions, session=session, timeout=1.0)
        if response.get("ok") is not True or not body:
            status = int(response.get("status") or HTTPStatus.SERVICE_UNAVAILABLE)
            return b'{"error":"status service unavailable"}', HTTPStatus(status)
        try:
            metadata = validate_status_snapshot(response, body)
        except StatusProtocolError:
            return b'{"error":"status service upgrade required"}', HTTPStatus.SERVICE_UNAVAILABLE
        return body, HTTPStatus(metadata.status)

    def stop_auto_approve_all(self) -> None:
        self.pricing_refresh_coordinator.stop_periodic()
        self.stats_current_runtime.stop()
        self.approval_client.request({"action": "shutdown"}, timeout=2.5)
        self.background_owner.stop()
        self.yoagent_controller.close_yoagent_codex_app_server()
        self.control_server.stop()

from __future__ import annotations

import collections
import copy
import ctypes
import errno
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
from functools import partial, wraps
from http import HTTPStatus
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Mapping
from urllib.parse import unquote
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from .infra import common
from . import local_service_projection
from .backend_health.observer import observed_health
from .search import file_index
from . import filesystem
from .filesystem import exclusions
from .filesystem.io_ops import read_json_file
from .workspace import session_files
from .workspace import published_caches
from .local_services import registry as local_services_registry
from .local_services.client import deferred_transport_errors
from .local_services.client import local_service_failure_is_transient
from .local_services.client import local_service_polling_capabilities
from .local_services.rpc import local_service_traffic_snapshot
from .local_services.runtime import local_service_exception_cause
from .stats_current import resolution as stats_resolution
from . import system_status_snapshot as system_status_snapshot_module
from .approval import yolo_rules
from .approval.approvals import blank_prompt_state  # noqa: F401 - compatibility re-export
from .approval.approvals import hybrid_approval_prompt_state
from .observability.activity_summary import activity_signature
from .observability.activity_summary import assemble_agent_window_rows
from .observability.activity_summary import build_recent_agents_payload
from .observability.activity_summary import build_global_activity_summary
from .observability.activity_summary import build_session_activity_summary
from .observability.activity_summary import recent_agent_paths_from_files
from .observability.activity_summary import yoagent_capabilities_payload
from .observability.failure_severity import failure_record_level
from .approval.approvald import ApprovalClient
from .approval.auto_approve_worker import auto_approve_lock_message
from .approval.auto_approve_worker import auto_approve_lock_message_fields
from .approval.auto_approve_worker import auto_approve_lock_owner
from .infra.background_owner import BACKGROUND_ROLE_SEARCH_INDEX
from .infra.background_owner import BACKGROUND_ROLE_SESSION_FILES
from .infra.background_owner import BACKGROUND_ROLE_STATS_SAMPLER
from .infra.background_owner import BACKGROUND_ROLE_TABBER_ACTIVITY
from .infra.background_owner import BACKGROUND_ROLE_WATCH_ROOTS
from .infra.background_owner import BackgroundOwnerRegistry
from .infra.background_owner import DisabledBackgroundOwner
from .infra.atomic_file import atomic_write_text
from .infra.atomic_file import file_lock
from .infra.cache import MISS as CACHE_MISS
from .infra.cache import TtlCache
from .infra.refresh_outcome import RefreshOutcome
from .infra.host_diagnostics import collect_host_diagnostics
from .infra.host_identity import current_host_identity
from .infra.host_partition import host_namespaced_path
from .infra.host_partition import host_partitioned_state_dir
from .client_events import CLIENT_EVENT_TYPES
from .client_events import ClientEventBroker
from .client_events import client_event_resource
from .client_events import normalize_client_event_client_id
from .observability.activity import ActivityLedger
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
from .login_rate_limit import LOGIN_THROTTLE_OVERRIDE_NAME
from .login_rate_limit import LoginRateLimiter
from .login_rate_limit import default_login_throttle_database_path
from .login_rate_limit import load_login_rate_policy
from .locales import message_descriptor
from .locales import message_fields
from .locales import normalize_locale
from .locales import user_message_payload
from .common import as_dict
from .common import next_numbered_session_name
from .common import tail_file_lines
from .common import truncate_text
from .common import yolomux_client_revision
from .control import YolomuxControlServer
from .control import send_yolomux_control_request
from .browser_diagnostic_receipts import JAVASCRIPT_MAX_SAFE_INTEGER
from .diagnostic_redaction import redact_diagnostic_value
from .search.search_indexer import SearchIndexerClient
from .jobd import JobClient
from .observability.pricing_catalog import PricingCatalog
from .observability.pricing_catalog import PricingRefreshCoordinator
from .observability.queued_delivery import QueuedDeliveryLedger
from .observability.queued_delivery import QueuedDeliveryCompactionOwner
from .stats_current.client import StatsCurrentClient
from .stats_current.client import iter_append_batches as stats_current_append_batches
from .stats_current import collectors as stats_current_collectors
from .stats_current import host_collectors as stats_current_host_collectors
from .stats_current import families as stats_current_families
from .stats_current.http import StatsHttpForwarder
from .stats_current.runtime import StatsCurrentRuntime
from .stats_current import storage as stats_current_storage
from .stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from .stats_current import usage as stats_current_usage
from .drop_actions import run_drop_action
from .observability.events import EventLog
from .observability.events import RunHistoryStore
from .observability.events import mutate_yolomux_state  # noqa: F401 - Yoagent dependency injection re-export
from .observability.events import search_snippet
from .observability.events import read_yolomux_state
from .observability.events import update_yolomux_state
from .server_logs import emit_server_log
from .tmux.agent_tui import classify_agent_pane
from .tmux.agent_tui import normalized_prompt_state
from .chat.chat_store import ChatStore
from .chat.chat_store import default_chat_database_path
from .chat.chat_service import default_chat_cursor_secret_path
from .chat.chat_service import CHAT_YOAGENT_INSTANCE_ID
from .chat.chat_service import CHAT_YOAGENT_USERNAME
from .chat.chat_service import ChatService
from .chat.chat_store import CHAT_TYPING_LEASE_SECONDS
from .metadata import MetadataCache
from .metadata import github_checks_unknown
from .metadata import git_inventory
from .metadata import invalidate_git_metadata_paths
from .metadata import indexed_repo_summaries
from .metadata import GIT_METADATA_CACHE_SECONDS
from .metadata import INDEXED_REPO_ROOTS_CACHE_SECONDS
from .metadata import metadata_build_cache
from .metadata import project_inventory
from .metadata import pull_request_number_from_subject
from .metadata import activity_work_summary_from_graph
from .metadata import session_work_graph
from .metadata import session_to_json
from .metadata import watched_pr_metadata
from .tmux.sessions import active_window_for_panes
from .tmux.sessions import discover_sessions
from .tmux.sessions import list_tmux_panes
from .tmux.sessions import discover_status_sessions
from .statusd_client import StatusClient
from .statusd_protocol import STATUSD_ACTIVITY_MAX_WORK_BYTES
from .statusd_protocol import activity_summary_disabled_response
from .statusd_protocol import activity_summary_enabled
from .statusd_protocol import require_activity_summary_enabled
from .statusd_protocol import StatusProtocolError
from .statusd_protocol import validate_activity_summary
from .statusd_protocol import validate_snapshot as validate_status_snapshot
from .watchd_client import WatchClient
from .watchd_protocol import WATCHD_DESCRIPTOR_RESYNC_SECONDS
from .watchd_protocol import WATCHD_DESCRIPTOR_TTL_SECONDS
from .watchd_protocol import WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS
from .watchd_protocol import WATCHD_SNAPSHOT_DEADLINE_SECONDS
from .watch_diff import payload_from_products as watch_diff_payload_from_products
from .watch_diff import responses_by_index as watch_diff_responses_by_index
from .settings import default_settings
from .settings import save_settings
from .settings import SETTINGS_PATH
from .settings import settings_payload
from .settings import summary_settings as normalized_summary_settings
from .settings import usage_pricing_profile as configured_usage_pricing_profile
from .observability.transcripts import codex_summary_prompt
from .observability.transcripts import compact_summary_lines
from .observability.transcripts import format_transcript_item
from .observability.transcripts import transcript_run_metadata
from .observability.transcripts import TRANSCRIPT_PARSER_GENERATION
from .observability.transcripts import terminal_input_counts_as_user_activity
from .observability.transcripts import trim_prompt_text
from .approval.prompt_detector import agent_screen_state
from .approval.prompt_detector import approval_prompt_state
from .tmux.tmux_utils import cmd_error
from .tmux.tmux_utils import list_tmux_session_names
from .tmux.tmux_utils import tmux
from .tmux.tmux_utils import tmux_clear_input  # noqa: F401 - Yoagent dependency injection re-export
from .tmux.tmux_utils import tmux_capture_pane
from .tmux.tmux_utils import tmux_capture_pane_styled
from .tmux.tmux_utils import tmux_has_exact_session
from .tmux.tmux_utils import tmux_paste_text  # noqa: F401 - Yoagent dependency injection re-export
from .tmux.tmux_utils import tmux_session_client_rows  # noqa: F401 - session-action compatibility seam
from .tmux.tmux_utils import tmux_session_target
from .tmux.session_retirement import SessionRetirementError
from .tmux.session_retirement import capture_tmux_session_retirement
from .tmux.session_retirement import join_tmux_session_retirement
from .tmux.tmux_theme import apply_tmux_theme_color_to_existing
from .tmux.tmux_theme import apply_tmux_theme_color_to_new_session
from .tmux.tmux_theme import tmux_theme_color_from_settings
from .tmux.tmux_signals import fetch_tmux_signal_snapshot
from .tmux.tmux_signals import TmuxSignalEventWatcher
from .tmux.tmux_signals import window_record_key
from .types import AutoApproveState
from .types import AutoApproveStatusPayload
from .types import RunHistoryEntry
from .types import RunHistoryPayload
from .types import SearchResult
from .types import SessionFilesPayload
from .state_services import ActivityTranscriptService
from .state_services import ClientEventWatcherRecord
from .state_services import ClientWatchDescriptor
from .state_services import ClientWatchRootValidationError
from .state_services import ClientWatchService
from .state_services import JobdOperationService
from .state_services import JobdOperationReservation
from .state_services import jobd_operation_lane
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
from .workdir import agent_auth_status
from .workdir import agent_auth_status_payload
from .workdir import available_agent_commands
from .workdir import available_terminal_commands
from .workdir import terminal_command
from .workdir import session_workdir
from .yoagent import backends as yoagent_backends
from .yoagent import conversation as yoagent_conversation
from .yoagent.backends import YOAGENT_STARTUP_QUESTION  # noqa: F401 - compatibility re-export
from .yoagent.backends import codex_event_session_id  # noqa: F401 - compatibility re-export
from .yoagent.backends import strip_yoagent_stream_hidden_thinking  # noqa: F401 - compatibility re-export
from .yoagent.backends import yoagent_activity_payload_signature  # noqa: F401 - compatibility re-export
from .yoagent.backends import yoagent_cli_fallback_reason  # noqa: F401 - compatibility re-export
from .yoagent.backends import yoagent_language_directive  # noqa: F401 - compatibility re-export
from .yoagent.actions import redacted_action_text
from .yoagent.preferences import yoagent_user_message_text
from .yoagent.skills import delete_user_skill_file
from .yoagent.skills import list_user_skill_files
from .yoagent.skills import load_yoagent_skills
from .yoagent.skills import read_user_skill_file
from .yoagent.skills import skill_validation_payload
from .yoagent.skills import write_user_skill_file
from .yoagent.skills import YoagentSkillValidationError
from .yoagent.transports import CodexAppServerSession
from .yoagent.transports import default_yoagent_transport_registry
from .yoagent.streaming import YoagentStreamPublisher
from .yoagent.conversation import sanitized_stream_items as sanitized_yoagent_stream_items
from .yoagent.controller import YOAGENT_JOB_POLL_SECONDS
from .yoagent.controller import YOAGENT_ACTION_RESULT_WAIT_SECONDS  # noqa: F401 - compatibility re-export
from .yoagent.controller import YOAGENT_JOBS_STATE_KEY  # noqa: F401 - compatibility re-export
from .yoagent.controller import YoagentController
from .yoagent.session_summaries import YOAGENT_SESSION_SUMMARIES_STATE_KEY  # noqa: F401 - compatibility re-export
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
    # A successful warm remains reusable until the metadata cache's normal TTL expires. Keep this
    # with the worker owner instead of rebuilding an equivalent per-request cache: tab/activity
    # status updates are not repository changes and must not resubmit GitHub/Linear/Git work.
    completed: dict[str, tuple[tuple[Any, ...], float]] = field(default_factory=dict)


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
# Agent-window git inventory (branch/dirty/ahead-behind rendered in Tabber/Info Bar) is re-spawned
# per repo on every tabber/auto-approve refresh. Cache it by the watcher dirty generation so a warm
# refresh over an unchanged repo skips the `git` spawn, with a short time backstop for watcher misses.
AGENT_WINDOW_GIT_INVENTORY_MAX_AGE_SECONDS = 10.0
AGENT_WINDOW_GIT_INVENTORY_CACHE_MAX = 128
# jobd `session_files_view` product wait budget for the owner-side background refresh worker. The
# worker runs in a dedicated thread, so a bounded block-poll here keeps the git/discovery CPU in the
# jobd worker process without ever touching an HTTP request thread.
SESSION_FILES_JOBD_JOB_DEADLINE_MS = 30_000
SESSION_FILES_JOBD_WAIT_SECONDS = 25.0
# A product can take seconds while jobd performs git/transcript work.  Polling its Unix socket every
# 50 ms from each owner-side worker was needless broker/web CPU; completed and stale products still
# return on the first read, while pending work is checked at this bounded shared cadence.
JOBD_PRODUCT_POLL_INITIAL_SECONDS = 0.25
JOBD_PRODUCT_POLL_MAX_SECONDS = 1.0


class SessionFilesJobdUnavailable(RuntimeError):
    """jobd could not materialize a session-files product (submit rejected or product not ready).

    Raised out of the owner-side compute so the single-flight record is released and NOTHING stale is
    cached; the next request re-triggers. It never falls back to inline git in the caller's thread.
    """

    def __init__(self, message: str, failure: dict[str, Any] | None = None):
        super().__init__(message)
        self.failure = copy.deepcopy(failure or {})


class JobdOperationUnavailable(RuntimeError):
    """An accepted jobd product could not reach one durable terminal result."""

    def __init__(
        self,
        message: str,
        failure: dict[str, Any] | None = None,
        *,
        code: str = "service_unavailable",
        status: HTTPStatus = HTTPStatus.SERVICE_UNAVAILABLE,
    ) -> None:
        super().__init__(message)
        self.failure = copy.deepcopy(failure or {"error": message})
        self.code = str(code)
        self.status = status


class JobdInteractionLease:
    """One reference-counted jobd client lease held across an active fs-batch/differ interaction.

    Measured W15 #4 root cause: under a saturated gate the fs-batch completion worker's product
    poll can be starved longer than the broker's idle window, so between two ``/api/fs/batch``
    calls the broker decides it is idle, removes its own socket, and the next relay fails with
    ``LocalRpcError: unattributed_latency`` -- the Finder shows "request failed".  A HELD client
    lease vetoes ``_idle_should_stop``/``shutdown_if_idle`` so the broker cannot vanish mid-session,
    while NOT weakening idle shutdown: once no interaction holds it, the broker still idles out and
    exits honestly.

    This reuses the ONE registry client-lease mechanism -- the same ``acquire_lease`` /
    ``release_lease`` the scheduler lease already uses -- so it is not a second lease type.  The
    lease is best-effort liveness, never a safety gate: if the acquire RPC cannot refresh it, the
    interaction proceeds unpinned rather than failing a healthy request.  Leases are reaped only
    when their holder process dies, so one acquire pins the broker until the matching release with
    no TTL refresh loop.
    """

    def __init__(self, job_client: JobClient) -> None:
        self._job_client = job_client
        self._lock = threading.Lock()
        self._holders = 0
        self._lease_id = ""

    def acquire(self) -> bool:
        """Add one interaction holder; take the shared client lease when it is the first."""
        with self._lock:
            if self._holders > 0:
                self._holders += 1
                return bool(self._lease_id)
            response = self._job_client.registry.acquire_lease(self._lease_id)
            lease_id = response.get("lease_id")
            if response.get("ok") is True and isinstance(lease_id, str) and lease_id:
                self._lease_id = lease_id
                self._holders = 1
                return True
            # Could not pin the broker this attempt.  Do NOT count a holder: a later acquire must
            # re-issue the RPC rather than assume a lease it never took.
            return False

    def release(self) -> None:
        """Drop one interaction holder; release the shared client lease when the last one leaves."""
        with self._lock:
            if self._holders == 0:
                return
            self._holders -= 1
            if self._holders == 0 and self._lease_id:
                self._job_client.registry.release_lease(self._lease_id)
                self._lease_id = ""

    @property
    def held(self) -> bool:
        """Whether a client lease is currently pinning the broker for an active interaction."""
        with self._lock:
            return bool(self._lease_id)


TABBER_ACTIVITY_JOBD_JOB_DEADLINE_MS = 15_000
TABBER_ACTIVITY_JOBD_WAIT_SECONDS = 20.0


class TabberActivityJobdUnavailable(RuntimeError):
    """jobd could not materialize a tabber-activity product for the changed-session batch.

    Raised so the caller can serve last-known-good per-session rows (or the bounded empty shape)
    instead of falling back to an in-process rebuild of the batch.
    """


# A fresh metadata warm may make several bounded Git and provider requests. Keep the web caller's
# 25-second responsiveness limit below this worker deadline so a completed product can still warm
# the next cycle, while retaining a hard upper bound on background work.
METADATA_WARM_JOBD_JOB_DEADLINE_MS = 60_000
METADATA_WARM_JOBD_WAIT_SECONDS = 25.0


class MetadataWarmJobdUnavailable(RuntimeError):
    """jobd could not materialize a metadata-warm product for the session batch.

    Raised so the caller skips this warm cycle entirely (the periodic warmer retries later) instead
    of falling back to an in-process GitHub/Linear network fetch or git spawn.
    """


class JobdProductRpcUnavailable(RuntimeError):
    """The broker could not answer a product read during a bounded owner-side wait."""


class ActivitySummaryStatusdUnavailable(RuntimeError):
    """statusd could not return one completed activity-summary body."""

    def __init__(self, response: dict[str, Any]):
        message = str(response.get("error") or "status service unavailable")
        super().__init__(message)
        self.response = copy.deepcopy(response)


def wait_for_jobd_product(
    job_client: JobClient,
    coalesce_key: str,
    generation: int,
    wait_seconds: float,
    *,
    stop_event: threading.Event | None = None,
) -> tuple[dict[str, Any] | None, bytes | None, str]:
    """Read one matching jobd product without spinning the owner worker while it is pending.

    Returns ``(meta, body, state)`` when the expected generation is ready or stale, and
    ``(None, None, state)`` when the fixed caller budget expires.  Broker transport failures remain
    distinct so callers can preserve their feature-specific unavailable error and fallback policy.
    """
    deadline = time.monotonic() + wait_seconds
    poll_seconds = JOBD_PRODUCT_POLL_INITIAL_SECONDS
    while stop_event is None or not stop_event.is_set():
        meta, body = job_client.product(coalesce_key)
        if not meta.get("ok"):
            raise JobdProductRpcUnavailable("jobd product rpc unavailable")
        state = str(meta.get("state") or "")
        if body and state in {"ready", "stale"} and int(meta.get("generation") or 0) >= generation:
            return meta, body, state
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, None, state
        wait_for = min(poll_seconds, remaining)
        if stop_event is not None:
            if stop_event.wait(wait_for):
                return None, None, "stopped"
        else:
            time.sleep(wait_for)
        poll_seconds = min(JOBD_PRODUCT_POLL_MAX_SECONDS, poll_seconds * 2.0)
    return None, None, "stopped"


# Bump when the SERIALIZED session-files record changes meaning, so a record written by an older
# build is rejected instead of rendered as current. v2 added `repos[].missing`: without the bump a
# pre-fix record for a retired worktree would keep drawing the old rendering for up to
# SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS after the fix shipped. This is record COMPATIBILITY and
# is deliberately separate from SESSION_FILES_CACHE_KEY_VERSION, which versions the logical key
# inputs; those did not change.
SESSION_FILES_CACHE_VERSION = 2
SESSION_FILES_CACHE_KEY_VERSION = 4
def default_session_files_cache_dir(state_dir: Path | None = None) -> Path:
    """Keep one host's session-file cache out of a shared home mount."""

    root = common.STATE_DIR if state_dir is None else Path(state_dir)
    return host_partitioned_state_dir(root) / "session-files-cache"


def default_tabber_activity_cache_dir(state_dir: Path | None = None) -> Path:
    """Keep one host's tabber activity cache out of a shared home mount."""

    root = common.STATE_DIR if state_dir is None else Path(state_dir)
    return host_partitioned_state_dir(root) / "activity-cache"


def default_background_client_events_path(state_dir: Path | None = None) -> Path:
    """Share follower replay events only among this host's web processes."""

    root = common.STATE_DIR if state_dir is None else Path(state_dir)
    return host_partitioned_state_dir(root) / "background-owner" / "client-events.json"


def default_session_files_operation_state_path(state_dir: Path | None = None) -> Path:
    """Persist accepted-operation receipts and terminals inside this isolated instance root."""
    root = common.STATE_DIR if state_dir is None else Path(state_dir)
    return host_partitioned_state_dir(root) / "operations" / "session-files.json"


SESSION_FILES_CACHE_DIR = default_session_files_cache_dir()
SESSION_FILES_OPERATION_STATE_PATH = default_session_files_operation_state_path()
SESSION_FILES_OPERATION_POLL_INITIAL_SECONDS = 0.05
SESSION_FILES_OPERATION_POLL_MAX_SECONDS = 0.5
SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
SESSION_FILES_DISK_CACHE_MAX_BYTES = 1024 * 1024 * 1024
SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS = 5 * 60
SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE = 256
SESSION_FILES_DISK_CACHE_INDEX_FILENAME = "cache-index.json"
SESSION_FILES_DISK_CACHE_INDEX_VERSION = 1
TABBER_ACTIVITY_CACHE_VERSION = 2
TABBER_ACTIVITY_CACHE_DIR = default_tabber_activity_cache_dir()
TABBER_ACTIVITY_CONSUMER_TTL_SECONDS = 30.0
TABBER_ACTIVITY_REFRESH_DEBOUNCE_SECONDS = 0.2
# Session-files cold rebuilds parse transcripts and run Git at the same time.  Two
# workers was the best p95 on the captured eight-session shape; more workers only
# increase disk/GIL/subprocess contention.  Preferences may reduce or raise this
# within the deliberately small safe range.
SESSION_FILES_BATCH_MAX_WORKERS = 2
SESSION_FILES_GIT_SNAPSHOT_MAX_ITEMS = 128
TRANSCRIPT_TAIL_CACHE_MAX_ITEMS = 128
TRANSCRIPTS_PAYLOAD_CACHE_SECONDS = 15.0
# A single-flight refresh worker that outlives this deadline is treated as stalled and
# may be superseded, so a hung heavy build cannot pin the guard and refuse every future
# refresh (which froze the aggregate session-metadata header indefinitely). Far above a
# healthy build, which is a handful of timeout-bounded git calls per indexed repo.
TRANSCRIPTS_PAYLOAD_WORKER_DEADLINE_SECONDS = 60.0
CONTEXT_ITEMS_CACHE_MAX_ITEMS = 128
CONTEXT_OPERATION_DEADLINE_SECONDS = 15.0
FS_BATCH_OPERATION_DEADLINE_SECONDS = 120.0
WATCHD_OPERATION_PRODUCT_LIMIT = 64
WATCHD_FAILURE_ACTIONS = frozenset({"acquire", "upsert", "remove", "wait_revision"})
WATCHD_FAILURE_CODES = frozenset({"deadline_expired", "handler_failed", "producer_failed", "service_unavailable", "stale_generation", "unknown_lease", "upgrade_required"})
WATCHD_FAILURE_LOG_GRACE_SECONDS = 2.0
SERVER_INTERACTIVE_EVENT_POLL_SECONDS = 1.5
SERVER_INTERACTIVE_EVENT_POLL_JITTER_SECONDS = 0.5
SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS = SERVER_INTERACTIVE_EVENT_POLL_SECONDS
SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS = SERVER_INTERACTIVE_EVENT_POLL_SECONDS
# A stopped SSE watcher gets a two-second teardown join. Keep the blocking RPC below that
# boundary so reconnects cannot strand obsolete waiters in statusd's bounded handler pool.
STATUS_GENERATION_RPC_WAIT_SECONDS = 1.0
TMUX_SIGNAL_REMOVAL_EVENT_TTL_SECONDS = 10.0
INPUT_HEARTBEAT_COALESCE_SECONDS = 0.05
# Essential = this server drives the service itself and a user-visible capability is wrong
# when it FAILS. A recorded failure is always reportable:
#   indexd    Quick Open results silently go stale -- the 0.7.0 QA incident.
#   jobd      every /api/fs/* request is executed there.
#   statusd   the tmux status/roster the session UI renders.
#   statsd    the YO!stats database writer.
#   approvald auto-approval; a dead approver must never read as "nothing to approve".
#   watchd    every attached client's live file/session updates come from its revisions.
# watchd used to be excluded here to stop its routine absence reading as an outage. That was a
# second copy of a rule the row already owns: `demand_started` (watchd_runtime_status) is what
# classifies a legitimately-absent demand-scoped service as "idle" in system_status_service, and
# it is checked before this set is consulted. Keeping the exclusion as well meant one rule lived
# in two places and could disagree -- and it also said, falsely, that a watchd which recorded a
# real failure was less important than the other five.
ESSENTIAL_LOCAL_SERVICES = frozenset({"indexd", "jobd", "statusd", "statsd", "watchd", "approvald"})

# THE ONE ABSENCE statsd MAY HAVE EXCUSED, AND ITS EXACT BOUND
# ------------------------------------------------------------
# statsd is pinned up by `StatsCurrentRuntime._supervise` in the elected background owner, so its
# absence is a verified outage -- once that pin has had its chance. It has not had it yet during
# the boot window between `stats_current_runtime.start()` (called from
# `handle_background_owner_acquired`) and the lease that actually spawns statsd.
#
# MEASURED on real isolated starts (managed instance, this host), relative to process launch.
# Before, with the observer armed ahead of the election (port 17781):
#   +0.632s  background-owner generation created (the election is DECIDED here)
#   +0.635s  observer's first completed cycle -> statsd published `down` / `service_absent`
#   +1.136s  statsd child process spawned
#   +1.622s  statsd wrote its service record and began serving
#   +4.696s  statsd published `ready` (the two-observation recovery debounce)
# 4.06 seconds of false "YO!stats is not running" at every boot, for a statsd that was never down.
#
# The two changes were then ablated separately, because "it went green" is not a cause:
#   this excuse alone, observer still armed first (17783): `down` at +1.005s -> STILL BROKEN. At
#       +1.005s `stats_current_runtime.start()` had not run yet, so there was no pin owner to
#       state the excuse. The ordering in `cli.main()` is what makes the fact available at all.
#   the ordering alone, excuse removed (17784): first cycle at +2.911s, statsd already serving,
#       no `down`. It closes the window on THIS host only because `start_background_owner()`
#       synchronously takes jobd's scheduler lease (~2.2s) while statsd needs ~1.6s -- a 1.3s
#       margin that is timing, not a guarantee.
#   both (17782): first cycle at +2.738s, statsd `starting` -> `ready` at +4.750s, no `down`.
# So the ordering is what closes the measured window and the excuse is what stops the guarantee
# from resting on that 1.3s margin: whenever the first cycle does land inside the pin window, the
# row states the reason instead of the observer inventing an outage.
#
# This is the DYNAMIC excuse (`absence_expected_reason`), never the static `demand_started` one:
# statsd is not demand-scoped, and saying it were would silence a real outage forever. The
# excuse is bounded by `statsd_pin_pending()` below so it cannot outlive the pending start.
STATSD_ABSENT_WHILE_PIN_PENDING = "stats_pin_pending"
# The supervisor phases that mean "this process is actively taking the statsd pin and has not
# taken it yet" (`stats_current/runtime.py:_supervise`). Deliberately NOT `waiting_owner`,
# `demoting`, `stopping`, `stopped`, `backoff` or `blocked`: every one of those means this
# process is not on its way to pinning statsd, and excusing them would let a statsd that died,
# or that a demoted/losing process can no longer see, stay silent forever.
STATSD_PIN_PENDING_PHASES = frozenset({"starting", "acquiring_lease", "starting_scheduler"})


def statsd_pin_pending(runtime_status: Mapping[str, Any]) -> bool:
    """Whether this process is mid-flight taking the statsd pin, so absence is not yet a failure.

    The one owner of statsd's expected-absence claim, read from the pin owner's own live status
    (`StatsCurrentRuntime.status()`) rather than from a timer or a boot grace period. Four
    conditions, all of which must hold, and each of which closes one silent-excuse hole:

    * ``supervisor.alive`` -- the pin owner thread exists at all. A process that lost the
      election never calls ``stats_current_runtime.start()``, so this is False there and an
      absent statsd stays `down`, which is correct: the winner is supposed to be keeping it up.
    * ``leased is not True`` -- the pin has not taken effect yet. Once it has, statsd exists and
      any later absence is an outage.
    * ``failure_count == 0`` -- the pin owner has recorded no failure. A statsd that is
      genuinely dead at boot fails ``acquire_lease``, which increments this and moves the phase
      to ``backoff``/``blocked``, so the excuse is withdrawn on the first failed attempt.
    * ``phase in STATSD_PIN_PENDING_PHASES`` -- it is in one of the three phases that lead to
      the lease, not one of the phases that mean it stopped, was demoted, or is backing off.

    The residual window is exactly one in-flight ``acquire_lease`` call, which is bounded by the
    registry's own start timeout; when that call fails the first condition set above is broken.
    """

    if not isinstance(runtime_status, Mapping):
        return False
    if runtime_status.get("leased") is True:
        return False
    supervisor = runtime_status.get("supervisor")
    if not isinstance(supervisor, Mapping):
        return False
    if supervisor.get("alive") is not True:
        return False
    if int(supervisor.get("failure_count") or 0) != 0:
        return False
    return str(supervisor.get("phase") or "") in STATSD_PIN_PENDING_PHASES


STATS_SAMPLE_CACHE_SECONDS = 0.95
STATS_AGENT_TOKEN_SAMPLE_SECONDS = 10.0
STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS = 60.0
# Deliberately the idle token cadence: the transcript enrich is re-run at most as often as the
# collector's own slowest sample, and any change to the unresolved-agent roster bypasses the TTL.
STATS_AGENT_TOKEN_ENRICH_MEMO_TTL_SECONDS = STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS
# One entry per distinct unresolved-agent roster. The roster changes only when a pane starts or
# stops, so this holds far more history than a live host produces; oldest-expiry entries evict.
STATS_AGENT_TOKEN_ENRICH_MEMO_MAX_ENTRIES = 64


def stats_current_usage_health(
    service_usage: Mapping[str, object],
    transcript_usage: Mapping[str, object],
    cadence_seconds: float,
    *,
    sampler_families: Mapping[str, object] | None = None,
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
    usage_stale_warning = visibly_appending and (
        last_accepted <= 0
        or (
            last_visible > last_accepted
            and accepted_age is not None
            and accepted_age > stale_bound
        )
    )
    sampler_warning = None
    sampler_rows = sampler_families if isinstance(sampler_families, Mapping) else {}
    for family, raw in sampler_rows.items():
        if not isinstance(raw, Mapping):
            continue
        last_failure = str(raw.get("last_failure") or "").strip()
        if not last_failure:
            continue
        cadence = max(0.0, float(raw.get("cadence_seconds") or 0.0))
        family_bound = max(stale_bound, cadence * 3.0 if cadence > 0 else stale_bound)
        last_attempt_at = max(0.0, float(raw.get("last_attempt_at") or 0.0))
        last_success_at = max(0.0, float(raw.get("last_success_at") or 0.0))
        attempts = max(0, int(raw.get("attempts") or 0))
        failures = max(0, int(raw.get("failures") or 0))
        active_loop = (
            last_attempt_at > 0
            and max(0.0, checked_at - last_attempt_at) <= family_bound
            and failures >= 3
            and attempts >= failures
            and (
                last_success_at <= 0
                or max(0.0, checked_at - last_success_at) > family_bound
            )
        )
        if not active_loop:
            continue
        warning = {
            "family": str(family),
            "last_failure": last_failure,
            "failures": failures,
            "attempts": attempts,
            "last_attempt_age_seconds": max(0.0, checked_at - last_attempt_at),
            "last_success_age_seconds": (
                max(0.0, checked_at - last_success_at)
                if last_success_at > 0
                else None
            ),
        }
        if sampler_warning is None or warning["last_attempt_age_seconds"] < sampler_warning["last_attempt_age_seconds"]:
            sampler_warning = warning
    warning_state = usage_stale_warning or sampler_warning is not None
    sampler_reason = (
        f"sustained sampler failure loop in {sampler_warning['family']}: "
        f"{sampler_warning['failures']} failures, last {sampler_warning['last_failure']}"
        if sampler_warning is not None
        else ""
    )
    if usage_stale_warning:
        reason = "transcripts are advancing but usage atoms are stale"
        if sampler_reason:
            reason = f"{reason}; {sampler_reason}"
    elif sampler_warning is not None:
        reason = sampler_reason
    elif visibly_appending:
        reason = "transcripts and usage atoms are advancing"
    else:
        reason = "no recent transcript growth"
    return {
        "state": "warning" if warning_state else ("ok" if visibly_appending else "idle"),
        "reason": reason,
        "stale_bound_seconds": stale_bound,
        "visibly_appending": visibly_appending,
        "last_visible_append_age_seconds": visible_age,
        "last_accepted_atom_age_seconds": accepted_age,
        "sampler_warning": sampler_warning,
    }
TMUX_AI_STATUS_VERSION = 1
STATS_HOST_RESOURCE_TIMEOUT_SECONDS = 0.75
_stats_host_fallback_warning_emitted = False
STATS_AGENT_ASK_STATES = frozenset({"approval", "needs-approval", "needs-input", "attention", "interrupted"})
STATS_AGENT_RUN_STATES = frozenset({"working"})
STATS_AGENT_TRANSITION_STATES = frozenset({"cooldown", "transition"})
STATS_AGENT_SESSION_STATE_PRIORITY = {"ask": 0, "run": 1, "transition": 2, "idle": 3}
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


def clamp_system_cpu_percent(value: float) -> float:
    """Normalize aggregate host CPU, which is always a capacity share."""

    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(100.0, value))


def normalize_process_cpu_percent(value: float) -> float:
    """Keep a process's per-core CPU share, which may exceed one core."""

    if not math.isfinite(value):
        return 0.0
    return max(0.0, value)


def clamp_gpu_utilization_percent(value: float) -> float:
    """Normalize a single GPU utilization reading, which is a device share."""

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
    return stats_current_host_collectors._darwin_system_times()


def system_cpu_percent_from_times(previous: tuple[float, float] | None, current: tuple[float, float] | None) -> float:
    if previous is None or current is None:
        return 0.0
    total_delta = current[0] - previous[0]
    busy_delta = current[1] - previous[1]
    if total_delta <= 0 or busy_delta < 0:
        return 0.0
    return clamp_system_cpu_percent((busy_delta / total_delta) * 100.0)


def current_system_cpu_percent_from_ps() -> float | None:
    """Web request threads never fork for stats; statsd owns host sampling."""

    return None


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


@dataclass(frozen=True, slots=True)
class DarwinSystemMemoryDetails:
    """Activity Monitor-style macOS memory facts from one VM snapshot."""

    physical_memory_bytes: int
    memory_used_bytes: int
    cached_files_bytes: int
    app_memory_bytes: int
    wired_memory_bytes: int
    compressed_memory_bytes: int
    swap_used_bytes: int | None
    pressure_percent: float | None
    pressure_level: int | None


class DarwinSwapUsage(ctypes.Structure):
    """macOS xsw_usage from sysctl vm.swapusage."""

    _fields_ = [
        ("total_bytes", ctypes.c_uint64),
        ("available_bytes", ctypes.c_uint64),
        ("used_bytes", ctypes.c_uint64),
        ("page_size", ctypes.c_uint32),
        ("encrypted", ctypes.c_int),
    ]


class DarwinVmStatistics64(ctypes.Structure):
    """macOS vm_statistics64_t fields used by the Memory card."""

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
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
        ("swapped_count", ctypes.c_uint64),
    ]


def darwin_sysctl_structure(name: str, value_type: type[ctypes.Structure]) -> ctypes.Structure | None:
    """Read one macOS struct sysctl without forking the sampler process."""
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
        if size.value < ctypes.sizeof(value):
            return None
        return value
    except (AttributeError, OSError):
        return None


def current_darwin_vm_statistics() -> tuple[int, int, DarwinVmStatistics64] | None:
    """Read the physical capacity, page size, and VM statistics in one Mach call."""
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
        return total, int(page_size.value), counters
    except (AttributeError, OSError):
        return None


def current_darwin_system_memory_snapshot() -> tuple[tuple[int, int], DarwinSystemMemoryDetails] | None:
    """Return legacy allocation and Activity Monitor facts from one native snapshot."""
    snapshot = current_darwin_vm_statistics()
    if snapshot is None:
        return None
    total, page_size, counters = snapshot

    def page_bytes(count: int) -> int:
        return max(0, min(total, int(count) * page_size))

    app_memory = page_bytes(counters.internal_page_count)
    wired_memory = page_bytes(counters.wire_count)
    compressed_memory = page_bytes(counters.compressor_page_count)
    cached_files = page_bytes(counters.external_page_count)
    memory_used = min(total, app_memory + wired_memory + compressed_memory)
    swap = darwin_sysctl_structure("vm.swapusage", DarwinSwapUsage)
    swap_used = int(swap.used_bytes) if swap is not None else None
    available_percent = darwin_sysctl_value("kern.memorystatus_level", ctypes.c_int)
    pressure_percent = None if available_percent is None else float(100 - max(0, min(100, available_percent)))
    native_pressure_level = darwin_sysctl_value("kern.memorystatus_vm_pressure_level", ctypes.c_int)
    pressure_level = native_pressure_level if native_pressure_level in {1, 2, 4} else None
    details = DarwinSystemMemoryDetails(
        physical_memory_bytes=total,
        memory_used_bytes=memory_used,
        cached_files_bytes=cached_files,
        app_memory_bytes=app_memory,
        wired_memory_bytes=wired_memory,
        compressed_memory_bytes=compressed_memory,
        swap_used_bytes=swap_used,
        pressure_percent=pressure_percent,
        pressure_level=pressure_level,
    )
    # The existing cross-platform series intentionally means physical allocation on
    # Darwin. Keep that legacy semantic separate from the pressure display.
    available = int(counters.free_count) * page_size
    return (total, max(0, total - min(total, available))), details


def current_darwin_system_memory_details() -> DarwinSystemMemoryDetails | None:
    """Return the Mac-only facts displayed together with the pressure graph."""
    snapshot = current_darwin_system_memory_snapshot()
    return None if snapshot is None else snapshot[1]


def current_darwin_system_memory_bytes() -> tuple[int, int] | None:
    """Read macOS physical allocation through Mach APIs for legacy consumers."""
    snapshot = current_darwin_system_memory_snapshot()
    return None if snapshot is None else snapshot[0]


# The GPU/hardware wrappers that used to sit here (`stats_GPU_gpu_metrics`,
# `stats_macos_gpu_metrics`, `stats_macos_hardware_metadata`, `stats_host_hardware_metadata` with
# its module-level cache, and `stats_gpu_metrics`) existed only to feed the unregistered
# `collect_current_stats_gpu` below. They were one-line re-wrappings of
# `yolomux_lib/stats_current/host_collectors.py`, which is the owner statsd actually calls, so
# removing the unwired collector left them with no caller. Their parsing coverage moved with them:
# the tests now drive `host_collectors.generic_gpu_devices` / `gpu_devices` /
# `macos_hardware_metadata` directly.


TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS = 1.009
TMUX_SIGNAL_ACTIVITY_WINDOW_SECONDS = 120.0
SERVER_WATCHED_PR_EVENT_POLL_SECONDS = 60.0
SERVER_ACTIVITY_HEARTBEAT_ROTATE_SECONDS = 3600.0
CLIENT_WATCH_ROOT_TTL_SECONDS = 300
CLIENT_WATCH_ROOT_LIMIT = 128
CLIENT_WATCH_FILE_LIMIT = 128
CLIENT_WATCH_ROOT_SURFACES_VERSION = 1
CLIENT_WATCH_ROOT_SURFACES = frozenset({
    "finder",
    "modified-files-parent",
    "modified-files-repository",
})
FILESYSTEM_WATCH_HISTORY_LIMIT = 64
FILESYSTEM_WATCH_HISTORY_SECONDS = 180.0
PERFORMANCE_RECORD_LIMIT = 4096
PERFORMANCE_RECENT_LIMIT = 120
PERFORMANCE_CAPTURE_RECORD_LIMIT = 2048
PERFORMANCE_SUMMARY_WINDOW_SECONDS = 60.0
SERVER_CPU_BUDGET_PERCENT = 30.0
# Below this share of the measured CPU, the profiled consumer list is not an explanation and
# the warning must say so instead of letting the top row read as the cause.
SERVER_CPU_BUDGET_ATTRIBUTION_MIN_PERCENT = 50.0
SERVER_CPU_BUDGET_SUSTAINED_SECONDS = 300.0
# The one reason the web process's own CPU/memory numbers are absent. `stats_cpu_sample` is
# their only writer, so before statsd's first accepted push nothing has measured this process.
# This is a STRUCTURAL absence, not a failure, and it must never be spelled `0`.
STATS_SAMPLE_NOT_PUSHED_REASON_CODE = "cpu_sample_not_pushed"
STATS_SAMPLE_NOT_PUSHED_REASON = (
    "statsd has not pushed a CPU sample to this web process yet, so its CPU and memory have not been measured"
)
# A sample that ARRIVED and then stopped. Distinct from never-pushed: delivery worked once, so the
# operator is looking for a stall, not a wiring problem. Freezing the last value instead would let
# a dead sampler read as a healthy idle process indefinitely.
STATS_SAMPLE_STALE_REASON_CODE = "cpu_sample_stale"
STATS_SAMPLE_STALE_REASON = (
    "the last CPU sample statsd pushed is {seconds}s old, so this process's CPU and memory are no longer being measured"
)
# A pushed sample carrying no timestamp. Every real producer stamps `time`, so this is a
# malformed record rather than a lifecycle state -- but its age is unknowable, and an unknowable
# age must not be rendered as a number.
STATS_SAMPLE_UNDATED_REASON_CODE = "cpu_sample_undated"
STATS_SAMPLE_UNDATED_REASON = (
    "the last CPU sample statsd pushed carries no timestamp, so it cannot be shown to describe the present"
)
BACKGROUND_REFRESH_EVENT_LOG_SAMPLE_EVERY = 25
BACKGROUND_CLIENT_EVENTS_PATH = default_background_client_events_path()
# The event's storage owner determines whether another server must be notified immediately.
# Keep this table next to the transport rather than letting each write path choose between a
# local publish and a poll-dependent refresh.
BACKGROUND_CLIENT_EVENT_POLICIES: dict[str, dict[str, str]] = {
    "attention_acks_changed": {"truth": "tmux-ai-status", "delivery": "push"},
    "auto_approve_changed": {"truth": "tmux workers and yolomux state", "delivery": "push"},
    "background_owner_changed": {"truth": "background-owner", "delivery": "push"},
    "background_refresh_done": {"truth": "background owner", "delivery": "push"},
    # Streaming Quick Open (step 5): a signal-only per-root progress nudge. Push delivery so a
    # FOLLOWER web process (not just the indexd-electing owner) receives it and can pull committed
    # deltas by cursor. The truth is the committed change journal in SQLite; the signal carries no
    # filesystem data, so persisting + fanning it out cannot disclose one client's paths to another.
    "search_progress": {"truth": "search change journal", "delivery": "push"},
    "chat_messages_changed": {"truth": "chat database", "delivery": "push"},
    "chat_typing_changed": {"truth": "chat database", "delivery": "push"},
    "event_log_changed": {"truth": "event log", "delivery": "push"},
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
# Streaming Quick Open follower drain: how often a web process with an open palette pulls indexd's
# buffered progress frames while a crawl is active, and how long "active" lasts after the last kick or
# unfinished frame. The window is bounded and only opens when the web itself enqueues/promotes a crawl,
# so an idle terminal never polls the daemon and never keeps it hot past its own idle timeout.
SEARCH_PROGRESS_DRAIN_POLL_SECONDS = 0.5
SEARCH_PROGRESS_ACTIVE_WINDOW_SECONDS = 30.0
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
    # Delivery identity, not content: it advances on every rebuild. Signing it would make the
    # change-detection signature differ on every pass and publish an unchanged payload each time.
    # `metadata_identity` is the same value as one object and carries the same generation, so it
    # has to be ignored for the same reason -- signing it would reintroduce the per-pass publish.
    "metadata_generation",
    "metadata_identity",
    "timings",
    "title",
    "working_elapsed_seconds",
})
CLIENT_EVENT_RECURRING_WORK_SPECS = {
    "status_generation_lease": {"class": "lease", "cadence_seconds": STATUS_GENERATION_RPC_WAIT_SECONDS},
    "attention_ack_fallback": {"class": "fallback", "cadence_seconds": SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS},
    "tmux_signal_fallback": {"class": "fallback", "cadence_seconds": SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS},
    "watched_pr_reconcile": {"class": "external-reconcile", "cadence_seconds": 60.0},
    "yoagent_job_reconcile": {"class": "lease", "cadence_seconds": YOAGENT_JOB_POLL_SECONDS},
}
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
XTERM_RUNTIME_ASSETS = ("xterm.js", "xterm.css", "xterm-addon-unicode11.js")


def xterm_runtime_assets_ready(root: str | Path) -> bool:
    root_path = Path(root)
    return all((root_path / "static" / "vendor" / name).is_file() for name in XTERM_RUNTIME_ASSETS)


def ensure_xterm_runtime_assets(root: str | Path) -> tuple[bool, str]:
    """Validate the tracked xterm vendor assets required by the runtime."""
    root_path = Path(root)
    missing = [f"static/vendor/{name}" for name in XTERM_RUNTIME_ASSETS if not (root_path / "static" / "vendor" / name).is_file()]
    return (False, f"tracked xterm vendor assets are missing: {', '.join(missing)}") if missing else (True, "")


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


@dataclass(frozen=True)
class JobdProductOperation:
    job_id: str
    product_key: str
    generation: int


@dataclass(frozen=True)
class FilesystemOperationHttpResponse:
    """Either a persisted cold receipt or one ready product for the HTTP writer."""

    payload: dict[str, Any] | None
    status: HTTPStatus
    body: bytes = b""
    product: dict[str, Any] | None = None
    transfer: "FilesystemArtifactTransfer | None" = None


@dataclass
class FilesystemArtifactTransfer:
    """One bounded lease over a broker-owned raw/zip artifact."""

    job_client: Any
    lease_id: str
    product: dict[str, Any]
    closed: bool = False

    def read(self, offset: int) -> bytes:
        metadata, chunk = self.job_client.artifact_chunk(self.lease_id, offset)
        if metadata.get("ok") is not True or metadata.get("offset") != offset or metadata.get("length") != len(chunk):
            raise OSError(str(metadata.get("error") or "invalid artifact chunk"))
        if metadata.get("sha256") != hashlib.sha256(chunk).hexdigest():
            raise OSError("artifact chunk integrity mismatch")
        return chunk

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.job_client.artifact_close(self.lease_id)


def filesystem_artifact_http_response(
    job_client: Any,
    product_key: str,
    generation: int,
    product: Mapping[str, Any],
) -> FilesystemOperationHttpResponse:
    """Open one exact broker product as a bounded HTTP transfer."""
    opened = job_client.artifact_open(product_key, generation)
    lease_id = str(opened.get("lease_id") or "")
    opened_product = opened.get("product") if isinstance(opened.get("product"), dict) else None
    if opened.get("ok") is not True or not lease_id or opened_product != dict(product):
        raise JobdOperationUnavailable(
            str(opened.get("error") or "jobd artifact unavailable"),
            dict(opened),
        )
    transfer = FilesystemArtifactTransfer(job_client, lease_id, dict(opened_product))
    return FilesystemOperationHttpResponse(None, HTTPStatus.OK, product=dict(opened_product), transfer=transfer)


@dataclass(frozen=True)
class FilesystemWatchBatchProduct:
    """One child batch of a partitioned watch-diff request.

    ``root_offset`` is the index of this chunk's first root inside the parent root list.  jobd
    numbers each batch's responses from zero, so the offset is what lets independently completing
    children merge back onto parent root order without colliding.
    """

    producer: JobdProductOperation
    ready_product: dict[str, Any] | None = None
    root_offset: int = 0
    root_count: int = 0


@dataclass
class FilesystemWatchReceiptFence:
    """Join a warm jobd product to the receipt persisted in parallel."""

    receipt_ready: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    operation_id: str = ""

    def accept(self, operation_id: str) -> None:
        self.operation_id = str(operation_id)
        self.receipt_ready.set()

    def cancel(self) -> None:
        self.cancelled.set()
        self.receipt_ready.set()

    def wait_for_operation_id(self) -> str:
        self.receipt_ready.wait()
        return "" if self.cancelled.is_set() else self.operation_id


@dataclass(frozen=True)
class TranscriptProductOperation(JobdProductOperation):
    cache_key: tuple[Any, ...]
    expected_generation: tuple[int, int]
    expected_identity: tuple[int, int]


@dataclass
class UpdateCheckRecord:
    """Bounded evidence for the one external self-update reconciler."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    enabled: bool = False
    attempts: int = 0
    useful: int = 0
    no_change: int = 0
    failures: int = 0
    last_attempt_at: float = 0.0
    last_useful_at: float = 0.0
    next_due_at: float = 0.0


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
        host_identity: Any | None = None,
    ) -> None:
        self.host_identity = host_identity
        self.path = host_namespaced_path(path, self.host_identity)
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
        return {
            "version": 2,
            "owner_id": self.owner_id,
            **(
                {
                    "stable_host_id": self.host_identity.stable_host_id,
                    "hostname": self.host_identity.display_hostname,
                }
                if self.host_identity
                else {}
            ),
            "entries": {},
            "updated_at": self._clock(),
        }

    def _read_payload(self) -> dict[str, Any]:
        raw = read_json_file(self.path, None, exceptions=(OSError, ValueError, TypeError))
        if raw is None:
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
        raw = read_json_file(self.owner_path, None, exceptions=(OSError, ValueError, TypeError))
        if raw is None:
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
            **(
                {
                    "stable_host_id": self.host_identity.stable_host_id,
                    "hostname": self.host_identity.display_hostname,
                }
                if self.host_identity
                else {}
            ),
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

    def _entry(self, path: str, source: str, expires_at: float, session: str = "", client_id: str = "") -> dict[str, Any]:
        item = {
            "path": path,
            "source": source,
            "expires_at": expires_at,
            "updated_at": self._clock(),
        }
        if session:
            item["session"] = session
        if client_id:
            item["client_id"] = client_id
        return item

    def _write_payload(self, payload: dict[str, Any]) -> None:
        atomic_write_text(self.path, json.dumps(payload, separators=(",", ":"), sort_keys=True), mode=0o600)

    def _write_owner_payload(self, payload: dict[str, Any]) -> None:
        owner_payload = {
            "version": 2,
            "owner_id": self.owner_id,
            **(
                {
                    "stable_host_id": self.host_identity.stable_host_id,
                    "hostname": self.host_identity.display_hostname,
                }
                if self.host_identity
                else {}
            ),
            "entries": payload.get("entries") if isinstance(payload.get("entries"), dict) else {},
            "updated_at": self._clock(),
        }
        atomic_write_text(self.owner_path, json.dumps(owner_payload, separators=(",", ":"), sort_keys=True), mode=0o600)

    def update_client_roots(self, roots: list[str], client_id: str = "") -> None:
        now = self._clock()
        expires_at = now + self.ttl_seconds
        normalized_client_id = normalize_client_event_client_id(client_id) or "legacy"
        with file_lock(self.owner_path):
            payload = self._read_owner_payload()
            entries = {
                key: entry
                for key, entry in self._live_owner_entries(payload.get("entries", {}), now).items()
                if isinstance(entry, dict) and not (entry.get("source") == "client" and str(entry.get("client_id") or "legacy") == normalized_client_id)
            }
            for path in roots[: self.limit]:
                entries[f"client:{normalized_client_id}:{path}"] = self._entry(path, "client", expires_at, client_id=normalized_client_id)
            payload["entries"] = entries
            self._write_owner_payload(payload)

    def remove_client_roots(self, client_id: str) -> None:
        """Release only one browser's roots; other tabs/processes remain intact."""
        normalized_client_id = normalize_client_event_client_id(client_id) or "legacy"
        now = self._clock()
        with file_lock(self.owner_path):
            payload = self._read_owner_payload()
            entries = {
                key: entry
                for key, entry in self._live_owner_entries(payload.get("entries", {}), now).items()
                if not (isinstance(entry, dict) and entry.get("source") == "client" and str(entry.get("client_id") or "legacy") == normalized_client_id)
            }
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
            raw = read_json_file(owner_file, None, exceptions=(OSError, ValueError, TypeError))
            if raw is None:
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
        raw = read_json_file(self.signature_path, (), exceptions=(OSError, ValueError, TypeError))
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


def immutable_watch_signature(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(immutable_watch_signature(item) for item in value)
    return value


def filesystem_paths_intersect(left: Path, right: Path) -> bool:
    return (
        left == right
        or filesystem._path_is_within(left, right)
        or filesystem._path_is_within(right, left)
    )


def filesystem_watch_product_signature(
    roots: list[str],
    products: list[dict[str, Any]],
) -> tuple[Any, ...]:
    responses = watch_diff_responses_by_index(products)
    signature = []
    for index, root in enumerate(roots):
        response = responses.get(index, {})
        raw_signature = response.get("watch_signature")
        item_signature = immutable_watch_signature(raw_signature)
        if (
            response.get("ok") is True
            and isinstance(item_signature, tuple)
            and item_signature
            and str(item_signature[0]) == root
        ):
            signature.append((root, item_signature))
            continue
        signature.append((root, (root, "error", int(response.get("status") or HTTPStatus.SERVICE_UNAVAILABLE))))
    return tuple(signature)


def filesystem_batch_submission(
    payload: dict[str, Any],
    *,
    key_prefix: str,
    identity_seed: str = "",
) -> tuple[dict[str, Any], str, list[Any]]:
    requests = filesystem.validated_batch_requests(payload)
    request_ids: list[Any] = []
    canonical_requests: list[Any] = []
    for index, request in enumerate(requests):
        request_ids.append(request.get("id", index) if isinstance(request, dict) else index)
        if not isinstance(request, dict):
            canonical_requests.append(copy.deepcopy(request))
            continue
        canonical_request = copy.deepcopy(request)
        canonical_request["id"] = index
        canonical_requests.append(canonical_request)
    canonical_payload = {
        **copy.deepcopy(payload),
        "requests": canonical_requests,
        # Captured HERE, on the accepting server's request thread, so the shared jobd worker
        # authorizes with this server's roots instead of its launcher's.  It sits inside the
        # canonical payload, so it is part of the product/coalescing identity below: two servers
        # with different policies can never share one retained batch product.
        filesystem.FS_ACCESS_POLICY_FIELD: filesystem.access_policy_descriptor(),
    }
    identity = hashlib.sha256(json.dumps(
        {"identity_seed": identity_seed, "payload": canonical_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:24]
    return canonical_payload, f"{key_prefix}:{identity}", request_ids


FILESYSTEM_RETAINED_READ_OPERATIONS = frozenset({
    "list", "read", "info", "search", "index_status", "count", "diff", "blame",
})

# Bounded single-target reads: one path in, a small answer out, and a browser waiting on the
# result right now (an editor open, a file probe, an index badge).  These are the only filesystem
# operations that take jobd's `point` lane.  Everything else -- recursive `list`, `search`,
# `count`, `diff`, `blame`, `delete`, Finder batches, watch-diff fanouts, forced session-files
# transforms -- stays on the shared `interactive` lane, because its cost is unbounded in the input
# and it is exactly the work that used to put an editor open behind it head-of-line on it.
FILESYSTEM_POINT_OPERATIONS = frozenset({"read", "info", "index_status"})

# The write-side half of that same principle, and the half that was missed when `point` was drawn:
# one path in, one bounded side effect, a browser waiting on it right now.  These satisfy exactly
# the `point` test but are NOT `point`, because `point` means a coalescable retained read -- the
# stat-derived content key and `fresh_only` are gated on `priority == "point"` -- while a mutation
# must never be coalesced with another mutation.  They take jobd's sibling `mutation` lane, which
# is bounded and physically separate from both the read lane and the shared `interactive` lane.
#
# `delete` is deliberately absent.  `delete_path` recurses through a whole subtree, so its cost is
# unbounded in the input; it belongs with the other unbounded work regardless of looking like a
# point mutation at the call site.  A measured `mkdir` waited 6737 ms then 8167 ms behind one
# recursive count over 457,364 files on the shared lane before this lane existed.
FILESYSTEM_BOUNDED_MUTATIONS = frozenset({"write", "rename", "mkdir"})


def filesystem_operation_priority(operation: str) -> str:
    """Return the one jobd lane priority that owns a filesystem operation."""
    name = str(operation)
    if name in FILESYSTEM_POINT_OPERATIONS:
        return "point"
    if name in FILESYSTEM_BOUNDED_MUTATIONS:
        return "mutation"
    return "interactive"


def filesystem_point_content_generation(path: str) -> tuple[str, str]:
    """Return one point read's content identity plus the reason it could not be derived.

    A point read's coalesce key must be deterministic, or two browsers opening the same file
    submit two jobs into a lane bounded at two.  It must also change the instant the file changes,
    or coalescing would hand back a retained product for content that no longer exists on disk.
    The file's own inode/size/mtime identity satisfies both, and the caller falls back to a
    non-coalescing key when it cannot be read rather than guessing at freshness.
    """
    try:
        stat = os.stat(os.path.normpath(os.path.expanduser(str(path))))
    except OSError as error:
        return "", f"stat_failed:{errno.errorcode.get(error.errno, error.errno)}"
    return f"stat:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}", ""


def filesystem_operation_descriptor(operation: str, path: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build the ONE filesystem job descriptor shape, policy included.

    Every filesystem descriptor -- retained read, uncoalesced submission, and byte relay -- is
    built here, because a descriptor that reaches the shared daemon without this server's captured
    access policy is executed with the launching server's authority instead of this one's.
    """
    return {
        "op": str(operation),
        "path": str(path),
        "args": copy.deepcopy(args),
        filesystem.FS_ACCESS_POLICY_FIELD: filesystem.access_policy_descriptor(),
    }


def filesystem_operation_submission(
    operation: str,
    path: str,
    args: dict[str, Any],
    *,
    scope: str,
    generation: str,
) -> tuple[dict[str, Any], str]:
    """Return one normalized, access-scoped retained filesystem-read descriptor."""
    canonical_payload = filesystem_operation_descriptor(
        operation,
        os.path.normpath(os.path.expanduser(str(path))),
        args,
    )
    identity = hashlib.sha256(json.dumps(
        {
            "scope": str(scope),
            "generation": str(generation),
            "payload": canonical_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:24]
    return canonical_payload, f"filesystem-operation:{identity}"


def filesystem_watch_batch_submission(
    roots: list[str],
    identity_seed: str,
) -> tuple[dict[str, Any], str]:
    requests = [
        {
            "id": index,
            "type": "list",
            "path": root,
            "trigger_counts": {"watch-diff": 1},
            "include_watch_signature": True,
        }
        for index, root in enumerate(roots)
    ]
    payload = {"requests": requests, "client_scope": "browser"}
    canonical_payload, product_key, _request_ids = filesystem_batch_submission(
        payload,
        key_prefix="fs-watch",
        identity_seed=identity_seed,
    )
    return canonical_payload, product_key


def filesystem_watch_request_product_key(roots: list[str], identity_seed: str) -> str:
    """Key the retained product of one whole watch-diff request, however many batches it took.

    Chunk keys come from ``filesystem_watch_batch_submission`` and are bounded by the per-job
    request limit.  A request of 65-128 roots has no single submission payload, so its retained
    product needs its own identity derived from the parent root list.
    """
    identity = hashlib.sha256(json.dumps(
        {
            "identity_seed": str(identity_seed),
            "roots": [str(root) for root in roots],
            # The child batches carry this server's access policy, so their parent's retained
            # product must not be shared with a server whose policy differs.
            "access_policy": filesystem.capture_access_policy().digest(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:24]
    return f"fs-watch-request:{identity}"


def filesystem_watch_product_at_offset(product: dict[str, Any], offset: int) -> dict[str, Any]:
    """Re-base one child batch product's response ids onto the parent root window.

    jobd numbers each submitted batch's responses from zero.  A partitioned watch-diff merges the
    children by response id, so every child but the first has to be shifted by the index of its
    first root before the merge, or later chunks would overwrite earlier ones.
    """
    if not offset:
        return product
    responses = product.get("responses")
    if not isinstance(responses, list):
        return product
    shifted: list[Any] = []
    for response in responses:
        if not isinstance(response, dict) or isinstance(response.get("id"), bool):
            shifted.append(response)
            continue
        try:
            response_id = int(response.get("id"))
        except (TypeError, ValueError):
            shifted.append(response)
            continue
        shifted.append({**response, "id": response_id + int(offset)})
    return {**product, "responses": shifted}


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


def metadata_warm_session_signature(info: SessionInfo) -> tuple[Any, ...]:
    """Return the repository-relevant subset of a session's live identity.

    Agent status, model, PID, and transcript offsets change frequently but do not change what a
    metadata warm resolves. Paths, commands, and agent identity do: they determine the worktrees
    and branches that can need GitHub/Linear enrichment.
    """
    panes = tuple(
        sorted(
            (pane.target, pane.current_path, pane.command, pane.process_label or "")
            for pane in info.panes
        )
    )
    agents = tuple(
        sorted(
            (agent.pane_target, agent.cwd or "", agent.kind, agent.command, agent.session_id or "")
            for agent in info.agents
        )
    )
    return info.session, panes, agents


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


class LocalServiceRecoveryControl:
    """The ONE object the backend-health observer may touch to recover a service.

    The observer holds no recovery primitive of its own: it calls ``retry(resource)`` on an
    injected control and nothing else (`backend_health/observer.py:_issue_retry`). This is that
    control, and its whole public surface is `retry`, so a `stop`, `restart`, `signal`,
    `unlink`, `reclaim` or `adopt` is not something the recovery path can reach even by
    accident. `tests/test_backend_health_recovery_wiring.py` asserts that surface and drives a
    client double that raises on every other attribute.

    It is a DISPATCHER, never a second recovery primitive. Every entrypoint it can reach is one
    of the client `retry` wrappers `tests/test_backend_health_catalog.py` already pins, and each
    of those bottoms out in `LocalServiceRegistry.retry`; this class adds no ladder, no backoff
    and no start of its own -- `ServiceRecoveryPlanner` owns when, and the client wrapper owns
    how. A service that is not in the map is not recoverable from here and says so by returning
    False rather than reaching for a registry the census does not know about.

    The map is resolved per call, not captured at construction, for the same reason
    `local_services_row_producers()` is: tests and runtime both replace client objects on the
    app, and a control bound once at boot would keep retrying the client that existed then.
    """

    def __init__(self, entrypoints: Callable[[], Mapping[str, Callable[[], bool]]]) -> None:
        self._entrypoints = entrypoints

    def retry(self, resource: str) -> bool:
        """Ask ONE named service's own client to clear its latched failure and come back."""

        entrypoint = self._entrypoints().get(str(resource))
        if entrypoint is None:
            # indexd is the one inventory service with no client-level retry wrapper. Reaching
            # into its registry from here would create a recovery entrypoint outside the wrapper
            # set the catalog pins, so recovery is simply not available for it.
            return False
        return bool(entrypoint())


class WatchBridge:
    """Cohesive watch/event behavior behind the TmuxWebtermApp facade."""
    def __init__(self, app: "TmuxWebtermApp") -> None:
        self._app = app
        self.state = app.__dict__.pop("__owned_state__watch_bridge_state", None) or ClientWatchService()
    def stop(self) -> None:
        self._app.stop_client_event_watcher()
    def wake_client_event_watcher(self, app) -> None:
        with self.state.lock:
            record = self.state.event_watcher_record
        record.wake_event.set()

    def note_client_event_recurring_work(self, app, record: ClientEventWatcherRecord, owner: str, *, useful: bool, failed: bool = False) -> None:
        """Record bounded recurring-work evidence beside the watcher that owns it."""
        if owner not in CLIENT_EVENT_RECURRING_WORK_SPECS:
            raise ValueError(f"unknown client-event recurring-work owner: {owner}")
        now = time.time()
        with self.state.lock:
            if self.state.event_watcher_record is not record:
                return
            entry = record.recurring_work.setdefault(owner, {
                "attempts": 0,
                "useful": 0,
                "no_change": 0,
                "failures": 0,
                "last_attempt_at": 0.0,
                "last_useful_at": 0.0,
            })
            entry["attempts"] = int(entry["attempts"]) + 1
            entry["last_attempt_at"] = now
            if failed:
                entry["failures"] = int(entry["failures"]) + 1
            if useful:
                entry["useful"] = int(entry["useful"]) + 1
                entry["last_useful_at"] = now
            else:
                entry["no_change"] = int(entry["no_change"]) + 1

    def client_event_recurring_work_snapshot(self, app, record: ClientEventWatcherRecord, now: float | None = None) -> list[dict[str, Any]]:
        """Return fixed-name timer diagnostics without paths, payloads, or client identity."""
        monotonic_now = time.monotonic() if now is None else float(now)
        next_due = {
            "filesystem_reconcile": record.next_signature_poll_at,
            "filesystem_fallback": record.next_file_poll_at,
            "status_generation_lease": 0.0,
            "attention_ack_fallback": record.next_attention_ack_poll_at,
            "tmux_signal_fallback": record.next_tmux_signal_poll_at,
            "watched_pr_reconcile": record.next_watched_pr_poll_at,
            "yoagent_job_reconcile": record.next_yoagent_job_poll_at,
        }
        rows: list[dict[str, Any]] = []
        for owner, spec in CLIENT_EVENT_RECURRING_WORK_SPECS.items():
            entry = record.recurring_work.get(owner, {})
            rows.append({
                "owner": owner,
                "class": spec["class"],
                "cadence_seconds": spec["cadence_seconds"],
                "demanded": app.client_event_recurring_work_demanded(owner),
                "attempts": int(entry.get("attempts") or 0),
                "useful": int(entry.get("useful") or 0),
                "no_change": int(entry.get("no_change") or 0),
                "failures": int(entry.get("failures") or 0),
                "last_attempt_at": float(entry.get("last_attempt_at") or 0.0),
                "last_useful_at": float(entry.get("last_useful_at") or 0.0),
                "next_due_in_seconds": max(0.0, float(next_due[owner] or 0.0) - monotonic_now),
            })
        return rows

    def client_event_recurring_work_demanded(self, app, owner: str) -> bool:
        channels = app.client_events.aggregate_channels()
        if owner in {"filesystem_reconcile", "filesystem_fallback"}:
            return not channels.isdisjoint({"files", "transcripts", "activity"})
        if owner in {"status_generation_lease", "attention_ack_fallback", "tmux_signal_fallback"}:
            return not channels.isdisjoint({"status", "attention"})
        if owner == "watched_pr_reconcile":
            return not channels.isdisjoint({"core", "attention"})
        return not channels.isdisjoint({"yoagent", "attention"})

    def client_event_watch_sleep_seconds(self, app, now: float, record: ClientEventWatcherRecord | None = None) -> float:
        current = record or self.state.event_watcher_record
        channels = app.client_events.aggregate_channels()
        deadlines: list[float] = []
        if not channels.isdisjoint({"status", "attention"}):
            if current.tmux_signal_refresh_at > 0:
                deadlines.append(current.tmux_signal_refresh_at)
            elif not app.tmux_signal_event_watcher_healthy():
                deadlines.append(current.next_tmux_signal_poll_at)
        if not channels.isdisjoint({"core", "attention"}):
            deadlines.append(current.next_watched_pr_poll_at)
            if now < app.search_progress_active_until:
                deadlines.append(current.next_search_progress_poll_at)
        if not channels.isdisjoint({"yoagent", "attention"}):
            deadlines.append(current.next_yoagent_job_poll_at)
        if not deadlines:
            return 60.0
        next_due = min(deadlines)
        if next_due <= 0:
            return app.server_event_poll_seconds()
        return max(0.01, min(60.0, next_due - now))

    def normalized_client_root_surfaces(
        self,
        payload: dict[str, Any],
        roots: list[str],
    ) -> tuple[int, tuple[tuple[str, tuple[str, ...]], ...]]:
        has_version = "root_surfaces_version" in payload
        has_rows = "root_surfaces" in payload
        if not has_version and not has_rows:
            # A running tab can retain the previous bundle until the server-version event asks it
            # to reload. Keep that bounded skew window functional, but mark it v0 instead of
            # inventing surface attribution the legacy browser never sent.
            return 0, ()
        if not has_version or not has_rows:
            raise ClientWatchRootValidationError("root surface version and rows must be provided together")
        version = payload.get("root_surfaces_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != CLIENT_WATCH_ROOT_SURFACES_VERSION:
            raise ClientWatchRootValidationError("unsupported root surface protocol version")
        raw_rows = payload.get("root_surfaces")
        if not isinstance(raw_rows, list) or len(raw_rows) > CLIENT_WATCH_ROOT_LIMIT:
            raise ClientWatchRootValidationError("root surfaces must be a bounded list")

        accepted_roots = set(roots)
        normalized: dict[str, tuple[str, ...]] = {}
        for row in raw_rows:
            if not isinstance(row, dict):
                raise ClientWatchRootValidationError("each root surface row must be an object")
            raw_path = row.get("path")
            path = str(raw_path or "").strip()
            if not path.startswith("/"):
                raise ClientWatchRootValidationError("root surface paths must be absolute")
            path = str(Path(path).expanduser())
            if path in normalized:
                raise ClientWatchRootValidationError("root surface paths must be unique")
            raw_surfaces = row.get("surfaces")
            if not isinstance(raw_surfaces, list) or not raw_surfaces or len(raw_surfaces) > len(CLIENT_WATCH_ROOT_SURFACES):
                raise ClientWatchRootValidationError("root surfaces must be a bounded non-empty list")
            surfaces = tuple(sorted(set(str(surface or "") for surface in raw_surfaces)))
            if not surfaces or any(surface not in CLIENT_WATCH_ROOT_SURFACES for surface in surfaces):
                raise ClientWatchRootValidationError("root surfaces contain an unknown surface")
            normalized[path] = surfaces

        if set(normalized) != accepted_roots:
            raise ClientWatchRootValidationError("root surfaces must exactly cover accepted roots")
        return CLIENT_WATCH_ROOT_SURFACES_VERSION, tuple(sorted(normalized.items()))

    def update_client_watch_roots(self, app, roots: Any) -> dict[str, Any]:
        now = time.monotonic()
        payload = roots if isinstance(roots, dict) else {"roots": roots}
        client_id = normalize_client_event_client_id(payload.get("client_id") if isinstance(payload, dict) else "")
        descriptor_id = client_id or f"legacy:{app.watch_root_owner_id}"
        raw_roots = payload.get("roots", []) if isinstance(payload, dict) else []
        unique = app.watch_root_index.normalize_paths(raw_roots)
        root_surfaces_version, root_surfaces = self.normalized_client_root_surfaces(payload, unique)
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
        context_items = app.normalized_client_context_items(payload.get("context_items", []))
        session_files_requests = app.normalized_client_session_files(payload.get("session_files", []))
        activity_summary = app.normalized_client_activity_summary(payload.get("activity_summary", {}))
        watch_update_started = time.perf_counter()
        # Transitional cross-web mirror for existing background readers. watchd
        # independently unions the leased descriptors and is the only watch owner.
        app.watch_root_index.update_client_roots(unique, descriptor_id)
        with self.state.lock:
            previous_descriptor = self.state.descriptors.get(descriptor_id)
            stable_descriptor = (
                tuple(unique),
                root_surfaces_version,
                root_surfaces,
                tuple(unique_files),
                tuple(unique_background_files),
                tuple(context_items),
                tuple(session_files_requests),
                activity_summary,
            )
            previous_stable = (
                previous_descriptor.roots,
                previous_descriptor.root_surfaces_version,
                previous_descriptor.root_surfaces,
                previous_descriptor.files,
                previous_descriptor.background_files,
                previous_descriptor.context_items,
                previous_descriptor.session_files,
                previous_descriptor.activity_summary,
            ) if previous_descriptor is not None else None
            descriptor_changed = previous_stable != stable_descriptor
            descriptor_generation = (
                previous_descriptor.descriptor_generation
                if previous_descriptor is not None and previous_stable == stable_descriptor
                else (previous_descriptor.descriptor_generation + 1 if previous_descriptor is not None else 1)
            )
            self.state.descriptors[descriptor_id] = ClientWatchDescriptor(
                expires_at=now + CLIENT_WATCH_ROOT_TTL_SECONDS,
                descriptor_generation=descriptor_generation,
                roots=tuple(unique),
                root_surfaces_version=root_surfaces_version,
                root_surfaces=root_surfaces,
                files=tuple(unique_files),
                background_files=tuple(unique_background_files),
                context_items=tuple(context_items),
                session_files=tuple(session_files_requests),
                activity_summary=activity_summary,
            )
        app.record_performance_sample(
            BACKGROUND_ROLE_WATCH_ROOTS,
            "client-roots-update",
            trigger="watch-roots-api",
            compute_ms=(time.perf_counter() - watch_update_started) * 1000,
            payload={"roots": unique, "files": unique_files, "background_files": unique_background_files, "client_bound": bool(client_id)},
            cache_status="updated",
            count=len(unique),
        )
        if descriptor_changed:
            app.wake_client_event_watcher()
        with app.client_events.lock:
            has_client_event_subscribers = bool(app.client_events.subscribers)
        if descriptor_changed and has_client_event_subscribers:
            app.start_client_watch_snapshot_publish()
        return {
            "ok": True,
            "roots": unique,
            "root_surfaces_version": root_surfaces_version,
            "root_surfaces": [
                {"path": path, "surfaces": list(surfaces)}
                for path, surfaces in root_surfaces
            ],
            "files": unique_files,
            "background_files": unique_background_files,
            "context_items": context_items,
            "session_files": session_files_requests,
            "activity_summary": activity_summary,
            "mode": "lifecycle" if client_id and app.client_events.has_client_id(client_id) else "ttl-fallback",
            "ttl_seconds": CLIENT_WATCH_ROOT_TTL_SECONDS,
        }

    def normalized_client_context_items(self, app, value: Any) -> list[dict[str, Any]]:
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
            messages = int(max(1, min(app.float_value(item.get("messages"), 200), MAX_COMPACT_TRANSCRIPT_ITEMS)))
            key = (session, messages)
            if key in seen:
                continue
            seen.add(key)
            items.append({"session": session, "messages": messages})
        return items[:MAX_YOLOMUX_SESSION_TABS]

    def normalized_client_session_files(self, app, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            session = str(item.get("session") or "").strip() or None
            hours = session_files.bounded_session_files_hours(app.float_value(item.get("hours"), 24.0))
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
            signature = app.client_event_payload_signature(request)
            if signature in seen:
                continue
            seen.add(signature)
            items.append(request)
        return items[:MAX_YOLOMUX_SESSION_TABS]

    def normalized_client_activity_summary(self, app, value: Any) -> dict[str, Any]:
        if not activity_summary_enabled():
            return {}
        if not isinstance(value, dict):
            return {}
        locale = normalize_locale(value.get("locale"))
        visible = value.get("visible") is True
        scope = app.normalized_activity_session_scope(value.get("scope"))
        hours = session_files.bounded_session_files_hours(app.float_value(value.get("hours"), 24.0))
        return {"locale": locale, "visible": visible, "scope": scope, "hours": hours}

    def client_watch_roots_snapshot(self, app) -> list[str]:
        app.prune_client_watch_descriptors()
        return app.watch_root_index.snapshot()

    def prune_client_watch_descriptors(self, app) -> None:
        now = time.monotonic()
        with self.state.lock:
            expired = [client_id for client_id, descriptor in self.state.descriptors.items() if descriptor.expires_at <= now]
            for client_id in expired:
                self.state.descriptors.pop(client_id, None)
        if expired:
            for client_id in expired:
                app.watch_root_index.remove_client_roots(client_id)
            app.wake_client_event_watcher()

    def touch_client_watch_descriptor(self, app, client_id: str) -> None:
        """Renew the orphan backstop from a live SSE stream, never from a browser interval."""
        normalized_client_id = normalize_client_event_client_id(client_id)
        if not normalized_client_id:
            return
        now = time.monotonic()
        with self.state.lock:
            descriptor = self.state.descriptors.get(normalized_client_id)
            if descriptor is None or descriptor.expires_at - now > CLIENT_WATCH_ROOT_TTL_SECONDS / 2:
                return
            descriptor.expires_at = now + CLIENT_WATCH_ROOT_TTL_SECONDS
            roots = list(descriptor.roots)
        app.watch_root_index.update_client_roots(roots, normalized_client_id)
        app.wake_client_event_watcher()

    def client_event_subscriber_disconnected(self, app, client_id: str) -> None:
        """Release a browser's watch descriptor after its final same-id SSE stream closes."""
        normalized_client_id = normalize_client_event_client_id(client_id)
        if not normalized_client_id or app.client_events.has_client_id(normalized_client_id):
            return
        with self.state.lock:
            self.state.descriptors.pop(normalized_client_id, None)
        app.watch_root_index.remove_client_roots(normalized_client_id)
        app.wake_client_event_watcher()

    def client_watch_file_paths(self, app, *, background: bool) -> list[str]:
        with self.state.lock:
            descriptors = list(self.state.descriptors.values())
            paths = {
                path
                for descriptor in descriptors
                for path in (descriptor.background_files if background else descriptor.files)
            }
        return sorted(paths)[:CLIENT_WATCH_FILE_LIMIT]

    def client_watch_files_snapshot(self, app) -> list[str]:
        return app.client_watch_file_paths(background=False)

    def client_watch_background_files_snapshot(self, app) -> list[str]:
        return app.client_watch_file_paths(background=True)

    def watchd_topology_signature(self, app) -> str | None:
        """Fingerprint the tmux topology the descriptor transcript set is derived from.

        One `tmux list-panes -a`: measured 0.57ms CPU / 5.24ms wall over 49 panes, against the
        25.8ms CPU of the discover_sessions it gates. It must never do the work it exists to
        avoid — no process table, no agent enrichment, no transcript stat or tail — so it sees
        exactly what tmux reports. A new session, a new or killed pane, a renamed session or
        window, and a pane whose foreground command changes all move it; an agent that starts
        writing a transcript without changing any of those does not, which is what
        WATCHD_DESCRIPTOR_RESYNC_SECONDS backstops.

        Returns None when tmux cannot be read, which never matches a stored signature and so
        forces the rebuild rather than pinning whatever was last derived.
        """

        panes, error = list_tmux_panes()
        if error:
            return None
        watched = sorted(app.sessions)
        rows = sorted(
            (pane.session, pane.window, pane.window_name, pane.pane, pane.pane_id, pane.command, pane.pid)
            for pane in panes
            if pane.session in set(watched)
        )
        return hashlib.sha256(repr((watched, rows)).encode("utf-8")).hexdigest()

    def watchd_transcript_paths(self, app) -> list[str]:
        """The transcripts descriptors watch, rebuilt on topology change or bounded reconcile.

        The revision loop calls this once per revision and the transcripts it returns are what
        produce those revisions, so deriving it every time was a feedback loop: 25.8ms of CPU a
        pass, which measured as ~90% of a core on the live server. Descriptor lease renewal stays
        independent and cheap; this discovery backstop shares watchd's 300-second loss-reconcile
        cadence instead of introducing a second polling owner.
        """

        signature = app.watchd_topology_signature()
        now = time.monotonic()
        service = self.state
        with service.lock:
            reusable = (
                signature is not None
                and signature == service.watchd_transcripts_signature
                and now - service.watchd_transcripts_at < WATCHD_DESCRIPTOR_RESYNC_SECONDS
            )
            if reusable:
                return list(service.watchd_transcripts)
        # discover_sessions runs outside the lock: it is the slow call this memo exists to
        # bound, and holding the watch service lock across it would stall every route that
        # touches a descriptor.
        service.note_owner_invocation("session_discovery")
        sessions, _errors = discover_sessions(app.sessions)
        transcripts = sorted({
            str(Path(agent.transcript).expanduser().resolve(strict=False))
            for info in sessions.values()
            for agent in info.agents
            if agent.transcript
        })[:CLIENT_WATCH_FILE_LIMIT]
        with service.lock:
            service.watchd_transcripts = list(transcripts)
            service.watchd_transcripts_signature = signature or ""
            service.watchd_transcripts_at = now
        return transcripts

    def watchd_descriptor_payloads(self, app) -> dict[str, dict[str, Any]]:
        """Build bounded daemon descriptors off the request path."""
        app.prune_client_watch_descriptors()
        transcripts = app.watchd_transcript_paths()
        settings = settings_payload().get("settings", {})
        file_explorer = settings.get("file_explorer", {}) if isinstance(settings, dict) else {}
        indexed_dirs = list(app.indexed_repo_discovery_dirs(file_explorer))
        # Same shared policy owner the Finder index and Differ ask. The watch daemon needs BOTH
        # halves: the directory-name half (skip_dirs) and the configured index_exclude_paths rules
        # (exclude_rules), so it can compile the FULL policy through this one owner and apply it at
        # native registration -- not a second ignore list inside watchd.
        exclusion_policy = exclusions.ExclusionPolicy.from_settings(
            file_explorer if isinstance(file_explorer, dict) else {},
            session_files.DEFAULT_INDEX_EXCLUDE_DIR_NAMES,
        )
        skip_dirs = sorted(exclusion_policy.skip_dir_names)
        exclude_rules = list(exclusion_policy.exclude_rules)
        configured_roots = [str(root) for root in filesystem._configured_fs_roots()]
        with app.session_files_service.cache_lock:
            repo_roots = sorted(app.session_files_service.repo_dirty_generations)
        with self.state.lock:
            descriptors = {
                descriptor_id: copy.deepcopy(descriptor)
                for descriptor_id, descriptor in self.state.descriptors.items()
            }
        expires_at = time.monotonic() + WATCHD_DESCRIPTOR_TTL_SECONDS
        return {
            descriptor_id: {
                "descriptor_generation": descriptor.descriptor_generation,
                "expires_at": expires_at,
                "roots": list(descriptor.roots),
                "files": list(descriptor.files),
                "background_files": list(descriptor.background_files),
                "transcripts": transcripts,
                "repo_roots": repo_roots,
                "indexed_dirs": indexed_dirs,
                "skip_dirs": skip_dirs,
                "exclude_rules": exclude_rules,
                "settings_path": str(SETTINGS_PATH.expanduser().resolve(strict=False)),
                "attention_path": str(app.tmux_ai_status_path.expanduser().resolve(strict=False)),
                "configured_roots": configured_roots,
            }
            for descriptor_id, descriptor in descriptors.items()
        }

    def apply_watchd_revision(self, app, record: ClientEventWatcherRecord, revision: dict[str, Any], *, reset: bool = False) -> list[str]:
        """Mirror compact daemon state and fan it into the existing SSE owners."""
        epoch = str(revision.get("epoch") or "")
        revision_number = int(revision.get("revision") or 0)
        watch_generation = int(revision.get("watch_generation") or 0)
        active_watch_generation = int(revision.get("active_watch_generation") or 0)
        changed_paths = [
            Path(path)
            for path in revision.get("changed_paths", [])
            if isinstance(path, str) and path.startswith("/")
        ]
        files_changed = revision.get("files_changed") if isinstance(revision.get("files_changed"), list) else []
        roots = tuple(str(root) for root in revision.get("roots", []) if isinstance(root, str))
        # watchd is a per-user daemon whose runtime socket derives from YOLOMUX_ROOT, not from a
        # server's YOLOMUX_FS_ROOTS.  Two servers with different filesystem policies under the same
        # YOLOMUX_ROOT therefore share ONE daemon, and its `wait_revision` takes no lease id and
        # returns the caller-independent UNION of every co-tenant's leased roots and change paths.
        # This consumer must scope that union to THIS server's own authorization boundary BEFORE it
        # mirrors any state or fans an `fs_changed` SSE, or a narrow-policy server would disclose a
        # broad co-tenant's roots and change activity to its own browser.  ``authorized_fs_roots()``
        # is the same boundary S0's access-policy descriptor authorizes content reads against, so a
        # root or path the daemon reports outside it is one this server may never publish or record.
        authorized_roots = tuple(
            filesystem._normalized_scope_path(root) for root in filesystem.authorized_fs_roots()
        )

        def _authorized_projection(path: Path) -> list[Path]:
            """Two-direction intersection of one reported path with this server's authorized roots.

            The daemon reports a co-tenant UNION, so a reported path may sit either INSIDE one of
            this server's roots or OUTSIDE-and-ABOVE it (a coarse/root report whose ancestor spans
            several tenants).  Both must resolve to a path this server is authorized for, and neither
            may ever surface the broad ancestor:
              * reported path is a descendant of (or equal to) an authorized root -> retain it;
              * an authorized root is a descendant of the reported path -> substitute that authorized
                root, so a coarse ancestor still delivers the server's OWN subtree without exposing
                the ancestor;
              * disjoint -> omit.
            """
            resolved = filesystem._normalized_scope_path(path)
            projected: list[Path] = []
            for authorized in authorized_roots:
                if filesystem._path_is_within(resolved, authorized):
                    projected.append(resolved)
                elif filesystem._path_is_within(authorized, resolved):
                    projected.append(authorized)
            return projected

        def _scope_paths(paths: list[Path]) -> list[Path]:
            scoped: list[Path] = []
            seen: set[str] = set()
            for path in paths:
                for projected in _authorized_projection(path):
                    key = str(projected)
                    if key not in seen:
                        seen.add(key)
                        scoped.append(projected)
            return scoped

        def _scope_generations(generations: dict[Any, Any]) -> dict[str, int]:
            """Re-key a daemon generation map onto authorized paths, carrying the source generation.

            A coarse ancestor key is substituted by the authorized descendant it covers, so the
            generation still drives this server's own cache invalidation without storing a co-tenant
            key it is not authorized to see.

            When several source keys project onto ONE authorized key -- an ancestor repo and the
            authorized repo itself both collapsing to the child -- their generations must compose
            LOSSLESSLY.  They are independent monotonic-within-epoch counters, so `max` would let a
            higher co-tenant counter mask the child's own increment (parent=100/child=5 and
            parent=100/child=6 both `max` to 100, so a real .git change on the server's OWN tree
            never invalidates).  Their sum is monotonic in every source, so any single source's
            increment strictly changes the composed value and still triggers invalidation, while the
            broad source path is still never exposed -- only the authorized child key is stored.
            """
            scoped: dict[str, int] = {}
            for key, generation in generations.items():
                if not isinstance(key, str):
                    continue
                value = int(generation or 0)
                for projected in _authorized_projection(Path(key)):
                    projected_key = str(projected)
                    scoped[projected_key] = scoped.get(projected_key, 0) + value
            return scoped

        def _within_authorized(path: Path) -> bool:
            resolved = filesystem._normalized_scope_path(path)
            return any(filesystem._path_is_within(resolved, authorized) for authorized in authorized_roots)

        daemon_reported_scope = bool(roots) or bool(changed_paths)
        roots = tuple(str(root) for root in _scope_paths([Path(root) for root in roots]))
        changed_paths = _scope_paths(changed_paths)
        # `files_changed` carries specific files, never coarse roots, so it stays exact-descendant
        # filtering: a file is mirrored only when it lives under an authorized root.
        files_changed = [
            entry
            for entry in files_changed
            if isinstance(entry, dict) and isinstance(entry.get("path"), str) and _within_authorized(Path(entry["path"]))
        ]
        # True only when the daemon reported roots/paths and authorization removed ALL of them --
        # a revision that touches only other tenants' roots.  A plain state revision that carried no
        # roots/paths to begin with is NOT this case and must still record normally.
        authorization_scoped_to_empty = daemon_reported_scope and not (roots or changed_paths)
        root_generations = _scope_generations(
            revision.get("root_generations") if isinstance(revision.get("root_generations"), dict) else {}
        )
        signature = tuple(
            (root, (root, "watchd", int(root_generations.get(root) or 0), watch_generation, ()))
            for root in sorted(roots)
        )
        token = str(revision.get("token") or f"{epoch}:{revision_number}")
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                return []
            if not reset and record.watchd_epoch == epoch and revision_number <= record.watchd_revision:
                return []
            if reset or record.watchd_epoch != epoch:
                self.state.filesystem_history.clear()
            previous_filesystem_roots = record.filesystem_roots
            record.watchd_epoch = epoch
            record.watchd_revision = revision_number
            record.watchd_applied_generation = watch_generation
            record.watchd_active_generation = active_watch_generation
            record.filesystem_healthy = bool(revision.get("healthy")) or bool(revision.get("fallback"))
            record.filesystem_roots = roots
            record.watchd_state = "polling" if revision.get("fallback") else ("ready" if record.filesystem_healthy else "errored")
            # Empty intersection (a revision that touches only other tenants' roots) leaves nothing
            # authorized to mirror: skip the signature update and the history write entirely so this
            # server records no co-tenant state, while the epoch/revision bookkeeping above still
            # advances so the revision loop is not wedged.
            if not authorization_scoped_to_empty:
                self.state.filesystem_signature = signature
                self.state.filesystem_history.append({
                    "token": token,
                    "created_at": float(revision.get("created_at") or time.time()),
                    "signature": signature,
                    "watchd_epoch": epoch,
                    "watchd_revision": revision_number,
                    "watch_generation": watch_generation,
                    "active_watch_generation": active_watch_generation,
                    "changed_paths": tuple(str(path) for path in changed_paths[:CLIENT_WATCH_FILE_LIMIT]),
                    "files_changed": copy.deepcopy(files_changed[:CLIENT_WATCH_FILE_LIMIT]),
                })
                self.state.filesystem_history = self.state.filesystem_history[-FILESYSTEM_WATCH_HISTORY_LIMIT:]
            # Scope the daemon's repo generations to authorized repos too: storing every co-tenant
            # repo key would mirror out-of-policy state and is a second disclosure surface.
            daemon_repo_generations = _scope_generations(
                revision.get("repo_generations") if isinstance(revision.get("repo_generations"), dict) else {}
            )
            prior_daemon_generations = self.state.watchd_repo_generations
            changed_repos = [
                repo
                for repo, generation in daemon_repo_generations.items()
                if int(generation or 0) != int(prior_daemon_generations.get(repo) or 0)
            ]
            self.state.watchd_repo_generations = {
                str(repo): int(generation or 0)
                for repo, generation in daemon_repo_generations.items()
            }
        if record.filesystem_healthy:
            app.publish_watchd_recovery(record)
        if changed_repos:
            with app.session_files_service.cache_lock:
                for repo in changed_repos:
                    if repo in app.session_files_service.repo_dirty_generations:
                        app.session_files_service.repo_dirty_generations[repo] += 1
        filesystem_roots = {Path(root) for root in (*previous_filesystem_roots, *roots)}
        filesystem_changed = any(
            filesystem_paths_intersect(path, root)
            for path in changed_paths
            for root in filesystem_roots
        )
        events: list[str] = []
        if changed_paths:
            app.mark_indexed_repo_discovery_dirty(changed_paths)
            # Invalidate from the admissible working-tree paths, not from ".git"
            # internals. Nothing beneath an ignored directory is published any
            # more, so a ".git"-only filter here selects nothing and the branch
            # and status caches would never be invalidated at all.
            # ``invalidate_git_metadata_paths`` already intersects each path
            # against the cached repository roots, so an ordinary file inside a
            # repository invalidates exactly that repository.
            invalidate_git_metadata_paths(changed_paths)
            # Item 6: feed native watchd change evidence into the ONE hot-path index owner so a file
            # created/modified/deleted outside YOLOmux (an external editor, a build) refreshes the
            # Quick Open index in seconds instead of waiting for the safety TTL. These paths are
            # already scoped to this server's authorized roots; the owner coalesces them by indexed
            # root and either promotes the frontier or runs one bounded subtree repair.
            filesystem.reindex_roots_for_paths([str(path) for path in changed_paths], reason="watchd")
        if revision.get("attention_changed"):
            events.extend(app.refresh_shared_attention_acks(trigger="watchd", notify_followers=True))
        if revision.get("settings_changed"):
            # Re-lease/enqueue when indexed-root settings change: added roots start layer-1 crawls,
            # removed roots release the scheduler obligation. Only the owner acts (guarded inside).
            app.refresh_search_indexer_schedule()
            app.publish_client_event("settings_changed", {"data": app.settings_payload()}, trigger="watchd", cache="ready")
            events.append("settings_changed")
        if revision.get("transcripts_changed"):
            app.clear_transcript_caches()
            app.publish_client_event("transcripts_changed", {"refresh": True}, trigger="watchd", cache="refresh")
            events.append("transcripts_changed")
        if files_changed:
            app.publish_client_event("files_changed", {"files": files_changed, "count": len(files_changed)}, trigger="watchd", cache="ready")
            events.append("files_changed")
        if revision_number > 0 and filesystem_changed:
            app.publish_client_event(
                "fs_changed",
                {
                    "roots": list(roots),
                    "mode": "diff",
                    "refresh": True,
                    "token": token,
                    "change_summary": {
                        "roots_changed": len(changed_paths),
                        "coarse": bool(revision.get("coarse")),
                    },
                    "daemon_state": record.watchd_state,
                },
                trigger="watchd",
                cache="ready",
            )
            events.append("fs_changed")
        if changed_paths:
            events.extend(app.publish_session_files_ready_events(trigger="watchd"))
        return events

    def publish_watchd_recovery(self, app, record: ClientEventWatcherRecord) -> None:
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                return
            recovered_episode = record.watchd_failure_episode
            if recovered_episode == 0:
                return
            recovered_started_at = record.watchd_failure_started_at
            recovered_count = record.watchd_failure_count
            recovered_delivery = record.watchd_failure_delivery
            recovered_published = record.watchd_failure_published
            record.watchd_failure_episode = 0
            record.watchd_failure_started_at = 0.0
            record.watchd_failure_count = 0
            record.watchd_failure_delivery = ""
            record.watchd_failure_action = ""
            record.watchd_failure_error_code = ""
            record.watchd_failure_published = False
        if not recovered_published:
            return
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                return
        recovery_seconds = min(86_400.0, max(0.0, time.monotonic() - recovered_started_at))
        emit_server_log(
            "info",
            "watchd",
            f"watchd recovered after {recovery_seconds:.1f}s and {recovered_count} failed attempt(s)",
            category="transport",
            dedupe_key=f"watchd-recovered:{recovered_episode}",
            request_id=f"watchd-episode-{recovered_episode}",
            route="local-service:watchd",
            event="watchd_recovered",
            delivery=f"recovered:{recovered_delivery}"[:64],
        )

    def publish_watchd_failure(self, app, record: ClientEventWatcherRecord, response: dict[str, Any], *, action: str) -> None:
        if action not in WATCHD_FAILURE_ACTIONS:
            raise ValueError("unknown watchd failure action")
        transport = str(response.get("_transport_error") or "")
        state = "not_running" if transport in {"absent", "refused"} else "errored"
        if transport:
            with self.state.lock:
                if self.state.event_watcher_record is not record or record.stop_event.is_set():
                    return
                record.watchd_state = state
                record.filesystem_healthy = False
            return
        retrying = bool(transport) or bool(response.get("retryable"))
        delivery = "retrying" if retrying else "failed"
        error_code = str(response.get("error_code") or "service_unavailable")
        if error_code not in WATCHD_FAILURE_CODES:
            error_code = "service_unavailable"
        now = time.monotonic()
        publish_failure = False
        failure_action = ""
        failure_error_code = ""
        failure_delivery = ""
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                return
            record.watchd_failure_count += 1
            if record.watchd_failure_episode == 0:
                record.watchd_failure_episode = record.watchd_next_failure_episode
                record.watchd_next_failure_episode += 1
                record.watchd_failure_started_at = now
                record.watchd_failure_delivery = delivery
                record.watchd_failure_action = action
                record.watchd_failure_error_code = error_code
            record.watchd_state = state
            record.filesystem_healthy = False
            if (
                not record.watchd_failure_published
                and now - record.watchd_failure_started_at >= WATCHD_FAILURE_LOG_GRACE_SECONDS
            ):
                record.watchd_failure_published = True
                publish_failure = True
                failure_action = record.watchd_failure_action
                failure_error_code = record.watchd_failure_error_code
                failure_delivery = record.watchd_failure_delivery
        if publish_failure:
            failure_retrying = failure_delivery == "retrying"
            emit_server_log(
                "warning" if failure_retrying else "error",
                "watchd",
                f"watchd {failure_action} failed ({failure_error_code}); retrying" if failure_retrying else f"watchd {failure_action} failed ({failure_error_code})",
                category="transport",
                dedupe_key=f"watchd-failure:{record.watchd_failure_episode}",
                request_id=f"watchd-episode-{record.watchd_failure_episode}",
                route="local-service:watchd",
                event=f"watchd_{failure_action}_failure",
                delivery=failure_delivery,
            )

    @staticmethod
    def record_watchd_synced_generation(record: ClientEventWatcherRecord, response: dict[str, Any]) -> None:
        """Own the one place a daemon response advances this client's rebuild window.

        Every response that names a watch generation is evidence about whether
        watchd still owes an activation, whichever client bumped it, so the
        window opens from an upsert this bridge issued and from a generation a
        different lease holder caused equally.
        """
        record.watchd_synced_generation = max(record.watchd_synced_generation, int(response.get("watch_generation") or 0))
        record.watchd_active_generation = max(record.watchd_active_generation, int(response.get("active_watch_generation") or 0))

    def sync_watchd_descriptors(self, app, record: ClientEventWatcherRecord) -> bool:
        descriptors = app.watchd_descriptor_payloads()
        active_ids = set(descriptors)
        descriptor_generations = {
            descriptor_id: int(descriptor.get("descriptor_generation") or 0)
            for descriptor_id, descriptor in descriptors.items()
        }
        for descriptor_id, descriptor in descriptors.items():
            response = app.watch_client.upsert(record.watchd_lease_id, descriptor_id, descriptor, reconfiguring=record.watchd_rebuild_window_open())
            if response.get("ok") is not True:
                app.publish_watchd_failure(record, response, action="upsert")
                return False
            # Record the bumped generation before the next request is armed: the
            # upsert that opens a rebuild window is answered before the daemon
            # blocks, so only the requests after it need the covering deadline.
            app.record_watchd_synced_generation(record, response)
        for descriptor_id in sorted(record.watchd_descriptor_ids - active_ids):
            response = app.watch_client.remove(record.watchd_lease_id, descriptor_id, reconfiguring=record.watchd_rebuild_window_open())
            if response.get("ok") is not True:
                app.publish_watchd_failure(record, response, action="remove")
                return False
            app.record_watchd_synced_generation(record, response)
        record.watchd_descriptor_ids = active_ids
        record.watchd_descriptor_generations = descriptor_generations
        app.publish_watchd_recovery(record)
        return True

    def watchd_revision_loop(self, app, record: ClientEventWatcherRecord) -> None:
        worker = threading.current_thread()
        iteration_started = 0.0
        try:
            while not record.stop_event.is_set() and not record.watchd_stop_event.is_set():
                # Pace the loop, not the work inside it. See
                # WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS: this loop's CPU is
                # body_cpu / loop_period, and a cheaper body only re-arms sooner, so no
                # amount of optimizing the body can bring it under budget. The remainder is
                # waited on the stop event rather than slept, so a shutdown breaks out of it
                # immediately instead of paying the floor. The first iteration never waits.
                remaining = WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS - (time.monotonic() - iteration_started)
                if remaining > 0 and record.watchd_stop_event.wait(remaining):
                    break
                if record.stop_event.is_set():
                    break
                iteration_started = time.monotonic()
                if not record.watchd_lease_id:
                    lease = app.watch_client.acquire_lease()
                    if lease.get("ok") is not True:
                        app.publish_watchd_failure(record, lease, action="acquire")
                        record.watchd_stop_event.wait(1.0)
                        continue
                    record.watchd_lease_id = str(lease.get("lease_id") or "")
                    record.watchd_pid = int(lease.get("pid") or 0)
                    record.watchd_epoch = str(lease.get("epoch") or "")
                    record.watchd_revision = 0
                    record.watchd_synced_generation = 0
                    record.watchd_applied_generation = 0
                    record.watchd_active_generation = 0
                    record.watchd_descriptor_generations = {}
                    # A rebuild another lease holder started is already in flight
                    # before this bridge issues anything, so the lease response is
                    # the first evidence of the window it has to arm against.
                    app.record_watchd_synced_generation(record, lease)
                if not app.sync_watchd_descriptors(record):
                    record.watchd_stop_event.wait(1.0)
                    continue
                response = app.watch_client.wait_revision(record.watchd_epoch, record.watchd_revision, timeout=2.0, reconfiguring=record.watchd_rebuild_window_open())
                if response.get("ok") is not True:
                    app.publish_watchd_failure(record, response, action="wait_revision")
                    if response.get("_transport_error") in {"absent", "refused"}:
                        record.watchd_lease_id = ""
                    record.watchd_stop_event.wait(1.0)
                    continue
                # A declared native-watch rebuild is an expected outcome carrying
                # its own reason, not a failure: the daemon cannot answer while it
                # registers, so the next request is armed against that window.
                if WatchClient.response_is_reconfiguring(response):
                    app.record_watchd_synced_generation(record, response)
                    app.publish_watchd_recovery(record)
                    record.watchd_stop_event.wait(WatchClient.reconfigure_backoff_seconds(response))
                    continue
                revision = response.get("revision") if isinstance(response.get("revision"), dict) else {}
                if response.get("changed") and revision:
                    app.apply_watchd_revision(record, revision, reset=bool(response.get("reset")))
                else:
                    app.publish_watchd_recovery(record)
        finally:
            lease_id = record.watchd_lease_id
            if lease_id:
                app.watch_client.release_lease(lease_id)
            with self.state.lock:
                if self.state.event_watcher_record is record and record.watchd_worker is worker:
                    record.watchd_worker = None
                    record.watchd_lease_id = ""
                    record.watchd_pid = 0
                    record.filesystem_healthy = False

    def watchd_runtime_status(self, app) -> dict[str, Any]:
        """Return the bridge mirror without making a status route call watchd.

        The rule that governs this row is unchanged and must stay: `WatchClient.runtime_status`
        exists, and calling it from here would issue a `status` RPC, which demand-starts a
        demand-scoped service from a diagnostics path. Nothing below issues any RPC.

        What changed in M2 is where the row's identity comes from. It used to be the bridge
        mirror alone, which knows a lease PID but no birth time, so `started_at` was hardcoded
        0.0 and the System view's Uptime cell was permanently blank, and `resources` was
        hardcoded {} so watchd was the one service with no CPU/memory at all. Both now come from
        the durable service record the registry already writes -- one file read plus a /proc
        read, identity-fenced against this host and boot, and no traffic to the daemon.

        The registry record is the identity owner, so its PID is the one this row reports and
        samples. When the bridge holds a lease whose PID the record cannot verify, that
        disagreement is published under `identity` rather than papered over by preferring
        whichever number looks healthier.
        """
        with self.state.lock:
            record = self.state.event_watcher_record
            state = record.watchd_state
            lease_id = record.watchd_lease_id
            epoch = record.watchd_epoch
            revision = record.watchd_revision
            bridge_pid = int(record.watchd_pid or 0)
        identity = local_service_projection.registry_process_identity(app.watch_client.registry)
        return local_service_projection.local_service_runtime_row(
            "watchd",
            pid=identity.pid,
            started_at=identity.started_at,
            version=identity.protocol_version,
            healthy=state in {"ready", "polling"},
            last_failure="" if state in {"starting", "ready", "polling"} else state,
            resources=app.watch_client.registry.resources(identity.pid),
            fields_before_failure={
                "clients": 1 if lease_id else 0,
                "queues": {"depth": 0},
                "cache": {"ready": revision > 0},
                "epoch": epoch,
                "revision": revision,
                "fallback": state == "polling",
                # watchd is spawned when a client attaches a watch and retires when the
                # last one detaches. "Absent" is its correct resting state, so it must not
                # read as an outage; only last_failure below can make it one. This is the ONE
                # owner of that rule -- ESSENTIAL_LOCAL_SERVICES deliberately no longer repeats it.
                "demand_started": True,
                "identity": {
                    **identity.as_dict(),
                    "source": "service_record",
                    "bridge_pid": bridge_pid,
                    # True only when the bridge names a PID the durable record does not confirm.
                    # A bridge with no lease yet is not a disagreement, it is just not started.
                    "bridge_pid_unverified": bridge_pid > 0 and bridge_pid != identity.pid,
                },
            },
        )

    def start_watchd_revision_watcher(self, app, record: ClientEventWatcherRecord) -> bool:
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                return False
            worker = record.watchd_worker
            if worker is not None and worker.is_alive():
                return False
            worker = threading.Thread(target=app.watchd_revision_loop, args=(record,), name="watchd-revision", daemon=True)
            record.watchd_worker = worker

        def rollback() -> None:
            with self.state.lock:
                if self.state.event_watcher_record is record and record.watchd_worker is worker:
                    record.watchd_worker = None
                    record.watchd_stop_event.set()

        common.start_thread_with_rollback(worker, rollback)
        return True

    def record_filesystem_watch_snapshot(self, app, signature: tuple[Any, ...]) -> str:
        now = time.time()
        with self.state.lock:
            if self.state.filesystem_history and self.state.filesystem_history[-1]["signature"] == signature:
                return str(self.state.filesystem_history[-1]["token"])
            signature_text = app.client_event_payload_signature(signature)
            digest = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:16]
            token = f"{int(now * 1000)}-{digest}"
            self.state.filesystem_history.append({
                "token": token,
                "created_at": now,
                "signature": copy.deepcopy(signature),
            })
            min_created_at = now - FILESYSTEM_WATCH_HISTORY_SECONDS
            self.state.filesystem_history = [
                record
                for record in self.state.filesystem_history[-FILESYSTEM_WATCH_HISTORY_LIMIT:]
                if float(record.get("created_at") or 0.0) >= min_created_at
            ]
            return token

    def filesystem_watch_record_for_token(self, app, token: str) -> dict[str, Any] | None:
        clean_token = str(token or "").strip()
        if not clean_token:
            return None
        with self.state.lock:
            for record in self.state.filesystem_history:
                if record.get("token") == clean_token:
                    return copy.deepcopy(record)
        return None

    def latest_filesystem_watch_record(self, app) -> dict[str, Any] | None:
        with self.state.lock:
            if self.state.filesystem_history:
                return copy.deepcopy(self.state.filesystem_history[-1])
        return None

    def filesystem_watch_signature_for_roots(
        self, app,
        roots: list[str],
    ) -> tuple[Any, ...]:
        return tuple(
            (root, filesystem.watch_signature(root, child_limit=filesystem.WATCH_SIGNATURE_CHILD_LIMIT))
            for root in roots[:CLIENT_WATCH_ROOT_LIMIT]
        )

    def filesystem_watch_full_plan(
        self, app,
        record: dict[str, Any],
        reason: str = "full",
    ) -> tuple[dict[str, Any], list[str]]:
        signature = record.get("signature")
        roots = sorted(filesystem_signature_root_map(signature).keys())
        return {
            "mode": "full",
            "reason": reason,
            "token": record.get("token", ""),
            "removed_roots": [],
        }, roots

    def filesystem_watch_diff_plan(
        self, app,
        since_token: str = "",
        force_full: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        if force_full:
            roots = app.client_watch_roots_snapshot()
            return {
                "mode": "full",
                "reason": "forced",
                "token": "",
                "removed_roots": [],
            }, roots
        current = app.latest_filesystem_watch_record()
        if current is None:
            roots = app.client_watch_roots_snapshot()
            if roots:
                return {
                    "mode": "full",
                    "reason": "snapshot-unavailable",
                    "token": "",
                    "removed_roots": [],
                }, roots
            return {"mode": "none", "token": "", "directories": [], "removed_roots": []}, []
        previous = app.filesystem_watch_record_for_token(since_token)
        if previous is None:
            return app.filesystem_watch_full_plan(current, "stale-since")
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
            }, []
        changed_roots, removed_roots = filesystem_changed_roots(previous_signature, current_signature)
        return {
            "mode": "diff",
            "token": current.get("token", ""),
            "since": previous.get("token", ""),
            "removed_roots": removed_roots,
            "change_summary": filesystem_change_summary(previous_signature, current_signature),
        }, changed_roots

    @staticmethod
    def decode_filesystem_watch_batch_product(body: bytes) -> dict[str, Any]:
        try:
            product = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JobdOperationUnavailable(
                "malformed completed filesystem batch product",
                {"error": str(error), "status": "malformed_product"},
            ) from error
        if not isinstance(product, dict) or not isinstance(product.get("responses"), list):
            raise JobdOperationUnavailable(
                "malformed completed filesystem batch product",
                {"error": "malformed completed filesystem batch product", "status": "malformed_product"},
            )
        return product

    def submit_filesystem_watch_batches(
        self, app,
        roots: list[str],
        identity_seed: str,
        *,
        delivery: str = "receipt",
    ) -> tuple[FilesystemWatchBatchProduct, ...]:
        """Partition one bounded root list into jobd batches and submit each exactly once.

        This is the only place watch roots are split.  Each chunk is a consecutive slice of the
        caller's root order, so chunk ``n`` owns roots ``[offset, offset + len(chunk))`` and its
        product response ids are re-based onto that window when the children are resolved.  Roots
        are never truncated or dropped: every accepted root reaches exactly one child batch.
        """
        batches: list[FilesystemWatchBatchProduct] = []
        for offset in range(0, len(roots), filesystem.MAX_BATCH_REQUESTS):
            chunk = roots[offset:offset + filesystem.MAX_BATCH_REQUESTS]
            batches.append(app.submit_filesystem_watch_batch(
                chunk,
                f"{identity_seed}#{offset}" if offset else identity_seed,
                offset=offset,
                delivery=delivery,
            ))
        return tuple(batches)

    def submit_filesystem_watch_batch(
        self, app,
        roots: list[str],
        identity_seed: str,
        *,
        offset: int = 0,
        delivery: str = "receipt",
    ) -> FilesystemWatchBatchProduct:
        """Submit one jobd batch for a chunk that already fits the per-job request limit."""
        if len(roots) > filesystem.MAX_BATCH_REQUESTS:
            raise JobdOperationUnavailable(
                f"filesystem watch batch must contain at most {filesystem.MAX_BATCH_REQUESTS} items",
                {
                    "error": f"filesystem watch batch must contain at most {filesystem.MAX_BATCH_REQUESTS} items",
                    "status": "invalid_request",
                    "roots": len(roots),
                    "maximum": filesystem.MAX_BATCH_REQUESTS,
                },
                code="invalid_request",
                status=HTTPStatus.BAD_REQUEST,
            )
        payload, product_key = filesystem_watch_batch_submission(roots, identity_seed)
        response, body = app.job_client.produce(
            "filesystem_batch",
            payload,
            priority="interactive",
            generation=1,
            coalesce_key=product_key,
            deadline_ms=int(FS_BATCH_OPERATION_DEADLINE_SECONDS * 1000),
            delivery=delivery,
        )
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        job_id = str(job.get("job_id") or "")
        state = str(job.get("status") or "")
        if response.get("ok") is not True or not job_id or state not in {"queued", "running", "completed"}:
            failure = dict(response)
            raise JobdOperationUnavailable(
                str(failure.get("error") or "jobd did not accept filesystem watch batch"),
                failure,
            )
        if body and delivery == "receipt":
            raise JobdOperationUnavailable(
                "receipt-only filesystem watch batch unexpectedly returned product bytes",
                {"error": "receipt-only filesystem watch batch unexpectedly returned product bytes"},
            )
        ready_product = app.decode_filesystem_watch_batch_product(body) if body else None
        return FilesystemWatchBatchProduct(
            producer=JobdProductOperation(job_id=job_id, product_key=product_key, generation=1),
            ready_product=ready_product,
            root_offset=int(offset),
            root_count=len(roots),
        )

    def filesystem_watch_batch_identity_seed(
        self, app,
        base_payload: dict[str, Any],
        roots: list[str],
    ) -> str:
        token = str(base_payload.get("token") or "")
        if token:
            return token
        with self.state.lock:
            latest = self.state.filesystem_history[-1] if self.state.filesystem_history else {}
            latest_token = str(latest.get("token") or "")
            latest_roots = sorted(filesystem_signature_root_map(latest.get("signature")).keys())
            if latest_token and latest_roots == sorted(roots):
                return latest_token
            return f"event-generation:{self.state.filesystem_event_generation}"

    def cached_filesystem_watch_products(self, app, product_key: str) -> list[dict[str, Any]] | None:
        with self.state.lock:
            record = self.state.filesystem_ready_product
            if product_key not in record.keys or not record.products:
                return None
            return [copy.deepcopy(product) for product in record.products]

    def cache_filesystem_watch_products(
        self, app,
        products: list[dict[str, Any]],
        product_keys: set[str],
    ) -> None:
        with self.state.lock:
            record = self.state.filesystem_ready_product
            record.keys = frozenset(str(key) for key in product_keys if str(key))
            record.products = tuple(copy.deepcopy(product) for product in products)

    def materialize_filesystem_watch_products(
        self, app,
        base_payload: dict[str, Any],
        roots: list[str],
        products: list[dict[str, Any]],
        *,
        product_keys: set[str],
    ) -> dict[str, Any]:
        signature = filesystem_watch_product_signature(roots, products)
        token = app.record_filesystem_watch_snapshot(signature)
        token_product_key = filesystem_watch_request_product_key(roots, token)
        app.cache_filesystem_watch_products(products, {*product_keys, token_product_key})
        return app.filesystem_watch_payload_from_products(
            {**copy.deepcopy(base_payload), "token": token},
            roots,
            products,
        )

    def resolve_filesystem_watch_batches(
        self, app,
        batches: tuple[FilesystemWatchBatchProduct, ...],
        deadline_at: float,
        *,
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve every child batch under one shared deadline and re-base it onto parent roots.

        Children may complete in any order; the merged product order is always the submission
        order, and each response keeps its own per-root cause because only its ``id`` is shifted.
        """
        products: list[dict[str, Any]] = []
        for batch in batches:
            if batch.ready_product is not None:
                products.append(filesystem_watch_product_at_offset(
                    copy.deepcopy(batch.ready_product),
                    batch.root_offset,
                ))
                continue
            product = app.wait_for_jobd_operation_product(
                batch.producer,
                deadline_at,
                cancel_event=cancel_event,
            )
            if not isinstance(product.get("responses"), list):
                raise JobdOperationUnavailable(
                    "malformed completed filesystem batch product",
                    {"error": "malformed completed filesystem batch product", "status": "malformed_product"},
                )
            products.append(filesystem_watch_product_at_offset(product, batch.root_offset))
        return products

    @staticmethod
    def filesystem_watch_payload_from_products(
        base_payload: dict[str, Any],
        roots: list[str],
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return watch_diff_payload_from_products(base_payload, roots, products)

    def complete_filesystem_watch_diff_operation(
        self, app,
        receipt_fence: FilesystemWatchReceiptFence,
        request_id: str,
        base_payload: dict[str, Any],
        roots: list[str],
        deadline_at: float,
        identity_seed: str,
    ) -> None:
        route = "GET /api/fs/watch-diff"
        operation = "jobd.produce"
        data: dict[str, Any] | None = None
        failure: tuple[dict[str, Any], str, HTTPStatus, str] | None = None
        # Hold the jobd interaction lease across the whole submit+product-poll window, exactly as
        # POST /api/fs/batch does (W15 #4).  Under a saturated gate this completion worker can be
        # starved between the submit ``produce`` and the product poll for longer than the broker's
        # idle window; the held lease vetoes the broker's idle shutdown so its socket cannot vanish
        # mid-interaction, which was the live ``GET /api/fs/watch-diff`` jobd-404.  This is the same
        # ONE lease owner fs/batch holds -- best-effort liveness, never a safety gate -- so the
        # ``try/finally`` always releases even when acquire could not pin the broker (release is
        # ref-counted and no-ops at holders==0).
        app.jobd_fs_batch_lease.acquire()
        try:
            batches = app.submit_filesystem_watch_batches(
                roots,
                identity_seed,
                delivery="ready_or_receipt",
            )
            operation = "jobd.product"
            products = app.resolve_filesystem_watch_batches(
                batches,
                deadline_at,
                cancel_event=receipt_fence.cancelled,
            )
            # The HTTP path looks the retained product up under the whole-request key, so a
            # partitioned request has to publish that key beside its per-chunk keys or the next
            # identical request would resubmit every child batch.
            request_product_key = filesystem_watch_request_product_key(roots, identity_seed)
            data = app.materialize_filesystem_watch_products(
                base_payload,
                roots,
                products,
                product_keys={request_product_key, *(batch.producer.product_key for batch in batches)},
            )
        except JobdOperationUnavailable as error:
            failure = (error.failure, operation, error.status, error.code)
        except Exception as error:
            failure = (
                {"error": str(error), "cause": local_service_exception_cause(error)},
                "filesystem-watch-diff.complete",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "producer_failed",
            )
        finally:
            app.jobd_fs_batch_lease.release()
        operation_id = receipt_fence.wait_for_operation_id()
        if not operation_id:
            return
        if failure is None:
            assert data is not None
            result = app.operation_ready_result(request_id, data)
            status = HTTPStatus.OK
        else:
            failure_payload, failure_operation, status, code = failure
            result = app.jobd_operation_failure_result(
                request_id,
                failure_payload,
                route=route,
                operation_id=operation_id,
                operation=failure_operation,
                code=code,
            )
        app.terminalize_operation(operation_id, result, status)

    def accept_filesystem_watch_diff_operation(
        self, app,
        request_id: str,
        base_payload: dict[str, Any],
        roots: list[str],
        identity_seed: str,
        reservation: JobdOperationReservation,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        deadline_at = time.time() + FS_BATCH_OPERATION_DEADLINE_SECONDS
        # The receipt is the only place the caller learns how much bounded work it is waiting on.
        # Submission itself stays on the completion worker, so this is a count, not a wait.
        batch_count = math.ceil(len(roots) / filesystem.MAX_BATCH_REQUESTS)
        receipt_fence = FilesystemWatchReceiptFence()
        submitted = app.jobd_operation_service.submit_reserved(
            reservation,
            app.complete_filesystem_watch_diff_operation,
            receipt_fence,
            request_id,
            base_payload,
            roots,
            deadline_at,
            identity_seed,
        )
        if not submitted:
            return app.jobd_operation_failure_result(
                request_id,
                {"error": "filesystem watch completion worker could not start"},
                route="GET /api/fs/watch-diff",
                operation="jobd.produce",
                code="producer_failed",
            ), HTTPStatus.SERVICE_UNAVAILABLE
        try:
            receipt = app.queued_delivery_ledger.accept_operation(
                request_id=request_id,
                route="GET /api/fs/watch-diff",
                deadline_at=deadline_at,
                progress={
                    "phase": "refreshing_snapshot" if not base_payload.get("token") else "waiting_for_product",
                    "producer": "jobd",
                    "producer_state": "submitting",
                    "batches_total": batch_count,
                },
                producer={
                    "service": "jobd",
                    "delivery": "ready_or_receipt",
                },
                kind="fs_watch_diff",
                context={
                    "mode": str(base_payload.get("mode") or ""),
                    "token": str(base_payload.get("token") or ""),
                    "since": str(base_payload.get("since") or ""),
                    "roots": len(roots),
                    "batches": batch_count,
                },
            )
        except Exception:
            receipt_fence.cancel()
            raise
        operation_id = str(receipt["operation"]["id"])
        receipt_fence.accept(operation_id)
        return receipt, HTTPStatus.ACCEPTED

    def filesystem_watch_diff_http_payload(
        self, app,
        since_token: str = "",
        force_full: bool = False,
        request_id: str = "",
    ) -> tuple[dict[str, Any], HTTPStatus]:
        request_id = str(request_id or app.new_api_request_id())
        base_payload, roots = app.filesystem_watch_diff_plan(since_token, force_full)
        if not roots:
            return base_payload, HTTPStatus.OK
        # The watch-root contract is CLIENT_WATCH_ROOT_LIMIT, not the per-job batch size: a
        # snapshot of 65-128 roots is accepted upstream by SharedWatchRootIndex and is split into
        # jobd batches of at most MAX_BATCH_REQUESTS by submit_filesystem_watch_batches().
        if len(roots) > CLIENT_WATCH_ROOT_LIMIT:
            return common.error_payload(
                f"filesystem watch roots must contain at most {CLIENT_WATCH_ROOT_LIMIT} items",
                message_key="request.error.tooManyItems",
                message_params={"field": "roots", "max": CLIENT_WATCH_ROOT_LIMIT},
                canonical=True,
                code="invalid_request",
                origin="server.http",
                retryable=False,
                details={"roots": len(roots), "maximum": CLIENT_WATCH_ROOT_LIMIT},
                stack=[{
                    "component": "server.http",
                    "operation": "GET /api/fs/watch-diff",
                    "code": "invalid_request",
                }],
                request_id=request_id,
            ), HTTPStatus.BAD_REQUEST
        identity_seed = app.filesystem_watch_batch_identity_seed(base_payload, roots)
        product_key = filesystem_watch_request_product_key(roots, identity_seed)
        cached_products = app.cached_filesystem_watch_products(product_key)
        if cached_products is not None:
            return app.materialize_filesystem_watch_products(
                base_payload,
                roots,
                cached_products,
                product_keys={product_key},
            ), HTTPStatus.OK
        reservation = app.jobd_operation_service.reserve("bulk")
        if reservation is None:
            result = app.jobd_operation_failure_result(
                request_id,
                {"error": "jobd operation completion pool is full", "status": "service_busy"},
                route="GET /api/fs/watch-diff",
                operation="jobd.produce",
                code="service_busy",
            )
            app.record_operation_failure("", result)
            return result, HTTPStatus.SERVICE_UNAVAILABLE
        return app.accept_filesystem_watch_diff_operation(request_id, base_payload, roots, identity_seed, reservation)

    def clear_transcript_content_caches(self, app) -> None:
        with app.activity_transcript_service.transcript_tail_cache_lock:
            app.activity_transcript_service.transcript_tail_cache.clear()
        with app.activity_transcript_service.context_items_cache_lock:
            app.activity_transcript_service.context_items_cache.clear()

    def clear_transcript_caches(self, app) -> None:
        app.clear_transcript_content_caches()
        with app.activity_transcript_service.transcripts_payload_cache_lock:
            record = app.activity_transcript_service.transcripts_payload_cache_record
            record.generation += 1
            record.stored_at = None
            record.payload = None
            # Invalidation supersedes the in-flight build, so it must release the whole guard, not
            # just the worker handle. Leaving `worker_started_at`/`publish_requested` set left an
            # intent behind that belonged to a caller this invalidation had already superseded.
            record.release_worker()
        # A queued follow-up build is a promise to a forced caller waiting on a named generation.
        # The invalidated worker can no longer keep it -- its finish is a generation mismatch and
        # returns before the drain -- so the promise was left sitting on the record until some
        # unrelated later build inherited it and published an extra follow-up. Drain it here through
        # the one owner instead: the caller gets the build it was promised, and the record is left
        # with no worker and no queued intent.
        app.start_queued_transcripts_payload_rebuild()

    def start_client_watch_snapshot_publish(self, app) -> bool:
        generation = 0
        worker: threading.Thread | None = None
        with self.state.lock:
            watcher_record = self.state.event_watcher_record
            if watcher_record.snapshot_worker is not None:
                return False
            def run() -> None:
                app.publish_client_watch_snapshot(watcher_record, generation)

            worker = threading.Thread(target=run, daemon=True)
            watcher_record.snapshot_worker = worker
            generation = app.begin_transcripts_payload_work(worker, replace=True)
        try:
            worker.start()
        except RuntimeError:
            with self.state.lock:
                if self.state.event_watcher_record is watcher_record and watcher_record.snapshot_worker is worker:
                    watcher_record.snapshot_worker = None
            app.finish_transcripts_payload_work(generation, worker, invalidate=True)
            raise
        return True

    def client_watch_snapshot_is_current(self, app, record: ClientEventWatcherRecord, worker: threading.Thread) -> bool:
        with self.state.lock:
            return (
                self.state.event_watcher_record is record
                and record.snapshot_worker is worker
                and not record.stop_event.is_set()
            )

    def publish_client_watch_snapshot(
        self, app,
        record: ClientEventWatcherRecord | None = None,
        generation: int | None = None,
    ) -> None:
        worker = threading.current_thread()
        guarded = record is not None
        if generation is None:
            generation = app.begin_transcripts_payload_work(worker, replace=True)
        try:
            started = time.perf_counter()
            payload = app.build_transcripts_payload()
            if guarded and not app.client_watch_snapshot_is_current(record, worker):
                return
            if not app.commit_transcripts_payload_cache(payload, generation):
                return
            signature = app.transcripts_payload_event_signature(payload)
            with self.state.lock:
                if guarded and (
                    self.state.event_watcher_record is not record
                    or record.snapshot_worker is not worker
                    or record.stop_event.is_set()
                ):
                    return
                previous_signature = self.state.transcripts_payload_signature
                self.state.transcripts_payload_signature = signature
            if previous_signature != signature:
                app.publish_client_event(
                    "transcripts_changed",
                    {"signature": signature, "refresh": True},
                    trigger="watch_state",
                    cache="ready",
                    compute_ms=(time.perf_counter() - started) * 1000,
                )
            if guarded and not app.client_watch_snapshot_is_current(record, worker):
                return
            app.publish_context_items_ready_events(trigger="watch_state")
            if guarded and not app.client_watch_snapshot_is_current(record, worker):
                return
            app.publish_activity_summary_ready_events(trigger="watch_state")
            if guarded and not app.client_watch_snapshot_is_current(record, worker):
                return
            app.publish_session_files_ready_events(trigger="watch_state")
        finally:
            app.finish_transcripts_payload_work(generation, worker)
            with self.state.lock:
                if guarded and self.state.event_watcher_record is record and record.snapshot_worker is worker:
                    record.snapshot_worker = None

    def record_dependency_invalidation(self, app, trigger: str) -> None:
        # Bounded by trigger reason (fs_changed, transcripts_changed, transcript_content_changed,
        # watch), never by event/session count, so this dict cannot grow with traffic volume.
        key = str(trigger or "watch")
        with self.state.lock:
            counts = self.state.invalidation_counts
            counts[key] = counts.get(key, 0) + 1

    def publish_context_items_ready_events(self, app, trigger: str = "watch") -> list[str]:
        app.prune_client_watch_descriptors()
        context_items, _session_files, _activity = self.state.snapshot()
        events: list[str] = []
        for item in context_items:
            started = time.perf_counter()
            payload, status = app.context_items(item["session"], int(item["messages"]), accept_pending=False)
            if status != HTTPStatus.OK or payload.get("pending"):
                continue
            event_payload = {"session": item["session"], "messages": item["messages"], "status": int(status), "data": payload}
            signature = app.client_event_payload_signature(event_payload)
            key = app.client_event_payload_signature({"session": item["session"], "messages": item["messages"]})
            with self.state.lock:
                previous_signature = self.state.context_item_payload_signatures.get(key)
                self.state.context_item_payload_signatures[key] = signature
            if previous_signature == signature:
                continue
            app.record_dependency_invalidation(trigger)
            app.publish_client_event(
                "context_items_ready",
                event_payload,
                trigger=trigger,
                cache="ready",
                compute_ms=(time.perf_counter() - started) * 1000,
            )
            events.append("context_items_ready")
        return events

    def publish_activity_summary_ready_events(self, app, trigger: str = "watch") -> list[str]:
        if not activity_summary_enabled():
            return []
        if str(trigger or "") not in ACTIVITY_SUMMARY_READY_PUSH_TRIGGERS:
            return []
        app.prune_client_watch_descriptors()
        _context_items, _session_files, activity_summary = self.state.snapshot()
        if activity_summary.get("visible") is not True:
            return []
        started = time.perf_counter()
        payload = app.activity_summary_payload(
            locale=str(activity_summary.get("locale") or "en"),
            session_scope=activity_summary.get("scope"),
            hours=activity_summary.get("hours"),
        )
        signature = app.stable_client_event_payload_signature(payload)
        with self.state.lock:
            previous_signature = self.state.activity_summary_signature
            self.state.activity_summary_signature = signature
        if previous_signature == signature:
            return []
        app.publish_client_event(
            "activity_summary_ready",
            {"locale": payload.get("locale", activity_summary.get("locale") or "en"), "data": payload},
            trigger=trigger,
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["activity_summary_ready"]

    def publish_session_files_ready_events(self, app, trigger: str = "watch", *, force: bool = False) -> list[str]:
        app.prune_client_watch_descriptors()
        _context_items, session_files_requests, _activity = self.state.snapshot()
        if force and not session_files_requests:
            session_files_requests = [
                {"session": session, "hours": 24.0}
                for session in app.sessions
            ]
        events: list[str] = []
        for item in session_files_requests:
            started = time.perf_counter()
            payload, status = app.session_files_payload(
                item.get("session"),
                app.float_value(item.get("hours"), 24.0),
                from_ref=item.get("from_ref"),
                to_ref=item.get("to_ref"),
                repo_refs=item.get("repo_refs"),
                # A watch event already advances the repository generation used by the shared
                # cache key. Keep last-known-good data and let its one background refresh
                # coalesce instead of turning every watcher notification into interactive Git.
                force=False,
                requester="background-refresh",
            )
            event_payload = {"request": item, "status": int(status), "data": payload}
            stable_event_payload = copy.deepcopy(event_payload)
            if isinstance(stable_event_payload.get("data"), dict):
                stable_event_payload["data"].pop("cache", None)
            signature = app.client_event_payload_signature(stable_event_payload)
            key = app.client_event_payload_signature(item)
            with self.state.lock:
                previous_signature = self.state.session_file_payload_signatures.get(key)
                self.state.session_file_payload_signatures[key] = signature
            if previous_signature == signature and not force:
                continue
            app.record_dependency_invalidation(trigger)
            app.publish_client_event(
                "session_files_ready",
                event_payload,
                trigger=trigger,
                cache="ready",
                compute_ms=(time.perf_counter() - started) * 1000,
            )
            events.append("session_files_ready")
        if events:
            app.request_tabber_activity_refresh(f"session-files:{trigger}")
        return events

    def start_status_generation_watcher(self, app, record: ClientEventWatcherRecord) -> bool:
        """Start one demand-scoped statusd generation waiter for this web process."""
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                return False
            worker = record.status_generation_worker
            if worker is not None and worker.is_alive():
                return True
            if time.monotonic() < record.status_generation_retry_at:
                return False
        lease = app.status_client.acquire_generation_lease()
        lease_id = str(lease.get("lease_id") or "") if lease.get("ok") is True else ""
        if not lease_id:
            with self.state.lock:
                if self.state.event_watcher_record is record:
                    record.status_generation_retry_at = time.monotonic() + 1.0
            return False
        response, _body = app.status_client.snapshot(app.sessions, timeout=1.0)
        if response.get("ok") is not True:
            app.status_client.release_generation_lease(lease_id)
            with self.state.lock:
                if self.state.event_watcher_record is record:
                    record.status_generation_retry_at = time.monotonic() + 1.0
            return False
        generation = max(0, int(response.get("generation") or 0))
        snapshot_payload: dict[str, Any] | None = None
        if _body:
            metadata = None
            try:
                metadata = validate_status_snapshot(response, _body)
                decoded_snapshot = json.loads(_body)
            except (StatusProtocolError, ValueError, TypeError):
                decoded_snapshot = None
            if metadata is not None and metadata.generation == generation and isinstance(decoded_snapshot, dict):
                snapshot_payload = decoded_snapshot
        with self.state.lock:
            if self.state.event_watcher_record is not record or record.stop_event.is_set():
                app.status_client.release_generation_lease(lease_id)
                return False
            record.status_generation_stop_event.clear()
            record.status_generation_lease_id = lease_id
            record.status_generation = generation
            self.state.auto_approve_signature = f"statusd:{generation}:{int(response.get('status') or HTTPStatus.OK)}"
            self.state.auto_approve_payload = copy.deepcopy(snapshot_payload)
            worker = threading.Thread(target=app.status_generation_wait_loop, args=(record,), name="statusd-generation-wait", daemon=True)
            record.status_generation_worker = worker
        common.start_thread_with_rollback(worker, lambda: app.stop_status_generation_watcher(record))
        return True

    def stop_status_generation_watcher(self, app, record: ClientEventWatcherRecord) -> None:
        with self.state.lock:
            worker = record.status_generation_worker
            lease_id = record.status_generation_lease_id
            record.status_generation_stop_event.set()
            record.status_generation_worker = None
            record.status_generation_lease_id = ""
        if lease_id:
            app.status_client.release_generation_lease(lease_id)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)

    def status_generation_wait_loop(self, app, record: ClientEventWatcherRecord) -> None:
        worker = threading.current_thread()
        retry_seconds = 0.25
        try:
            while not record.stop_event.is_set() and not record.status_generation_stop_event.is_set():
                with self.state.lock:
                    if self.state.event_watcher_record is not record:
                        return
                    generation = record.status_generation
                response = app.status_client.probe_generation(generation)
                if response.get("ok") is True:
                    next_generation = max(0, int(response.get("generation") or 0))
                    changed = response.get("changed") is True and next_generation > generation
                    app.note_client_event_recurring_work(record, "status_generation_lease", useful=changed)
                    if changed:
                        event_payload: dict[str, object] = {
                            "status": int(HTTPStatus.OK),
                            "refresh": True,
                            "generation": next_generation,
                            "signature": f"statusd:{next_generation}:{int(HTTPStatus.OK)}",
                        }
                        snapshot_response, snapshot_body = app.status_client.snapshot(app.sessions, timeout=1.0)
                        snapshot_payload = None
                        if snapshot_response.get("ok") is True and snapshot_body:
                            metadata = None
                            try:
                                metadata = validate_status_snapshot(snapshot_response, snapshot_body)
                                snapshot_payload = json.loads(snapshot_body)
                            except (StatusProtocolError, ValueError, TypeError):
                                snapshot_payload = None
                            if metadata is not None and metadata.generation == next_generation and isinstance(snapshot_payload, dict):
                                event_payload["refresh"] = False
                        with self.state.lock:
                            if self.state.event_watcher_record is not record:
                                return
                            previous_payload = copy.deepcopy(self.state.auto_approve_payload)
                            record.status_generation = next_generation
                            self.state.auto_approve_signature = f"statusd:{next_generation}:{int(HTTPStatus.OK)}"
                            if isinstance(snapshot_payload, dict):
                                self.state.auto_approve_payload = copy.deepcopy(snapshot_payload)
                        if isinstance(snapshot_payload, dict):
                            patch = app.auto_approve_client_event_patch(previous_payload, snapshot_payload)
                            if patch is None:
                                retry_seconds = 0.25
                                continue
                            event_payload = {**event_payload, **patch}
                        app.publish_client_event("auto_approve_changed", event_payload, trigger="statusd-generation", cache="ready")
                    retry_seconds = 0.25
                    if not changed and record.status_generation_stop_event.wait(STATUS_GENERATION_RPC_WAIT_SECONDS):
                        return
                    continue
                if record.status_generation_stop_event.wait(retry_seconds):
                    return
                app.note_client_event_recurring_work(record, "status_generation_lease", useful=False, failed=True)
                retry_seconds = min(5.0, retry_seconds * 2.0)
        finally:
            with self.state.lock:
                if self.state.event_watcher_record is record and record.status_generation_worker is worker:
                    record.status_generation_worker = None

    def poll_tmux_signals_client_event_once(self, app) -> list[str]:
        started = time.perf_counter()
        payload = app.tmux_signal_snapshot(force=True)
        signature = app.stable_client_event_payload_signature(app.tmux_signal_signature_payload(payload))
        with self.state.lock:
            previous = self.state.tmux_signal_signature
            previous_payload = copy.deepcopy(self.state.tmux_signal_payload) if self.state.tmux_signal_payload is not None else None
            self.state.tmux_signal_signature = signature
            self.state.tmux_signal_payload = copy.deepcopy(payload)
        if not previous:
            return []
        if previous == signature:
            return []
        event_payload = app.tmux_signal_patch_payload(previous_payload, payload)
        app.publish_client_event(
            "tmux_signals_changed",
            event_payload,
            trigger="timer",
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        app.request_tabber_activity_refresh("tmux-signals")
        return ["tmux_signals_changed"]

    def handle_tmux_signal_event(self, app, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or event.get("event") or "")
        if event_type in {"output", "extended-output"}:
            output_snapshot_at = time.monotonic() + TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS
            with self.state.lock:
                record = self.state.event_watcher_record
                next_snapshot_at = record.tmux_signal_refresh_at
                schedule_snapshot = next_snapshot_at <= time.monotonic() or next_snapshot_at > output_snapshot_at
                if schedule_snapshot:
                    record.tmux_signal_refresh_at = output_snapshot_at
            if schedule_snapshot:
                # Terminal bytes already travel on their own WebSocket. Coalesce the metadata
                # invalidation so a busy pane cannot launch a full tmux snapshot per output frame.
                app.tmux_signal_cache.clear()
                record.wake_event.set()
            return
        if event_type in {"pane-exited", "pane-died", "window-close", "sessions-changed"}:
            event_time = float(event.get("time") or time.time())
            with self.state.lock:
                self.state.tmux_signal_removal_event = {"type": event_type, "time": event_time}
            # The retained statusd roster is the sole agent-window authority. A topology event
            # must retire it so its next snapshot cannot keep a dead pane as a transition row.
            app.status_client.invalidate("tmux-topology")
        app.tmux_signal_cache.clear()
        with self.state.lock:
            record = self.state.event_watcher_record
            record.tmux_signal_refresh_at = time.monotonic()
        record.wake_event.set()

    def tmux_signal_event_watcher_healthy(self, app) -> bool:
        return app.tmux_signal_event_watcher_status().get("state") == "attached"

    def tmux_signal_event_watcher_status(self, app) -> dict[str, Any]:
        with self.state.lock:
            watcher = app.tmux_signal_event_watcher
        status = TmuxSignalEventWatcher.never_started_status() if watcher is None else watcher.status_payload()
        status["demanded"] = int(app.client_events.snapshot().get("subscribers") or 0) > 0
        return status

    def log_tmux_signal_event_error(self, app, message: str) -> None:
        app.log_event(
            None,
            "tmux_signal_event_error",
            message,
            {"diagnostic": message},
            message_key="events.message.tmuxSignalEvent.watchFailed",
        )

    def start_tmux_signal_event_watcher(self, app) -> bool:
        with self.state.lock:
            current = app.tmux_signal_event_watcher
            if current is not None and current.thread is not None and current.thread.is_alive():
                return False
            watcher = TmuxSignalEventWatcher(lambda: list(app.sessions), app.handle_tmux_signal_event, app.log_tmux_signal_event_error)
            app.tmux_signal_event_watcher = watcher
        if current is not None:
            current.stop()
        return watcher.start()

    def stop_tmux_signal_event_watcher(self, app) -> None:
        with self.state.lock:
            watcher = app.tmux_signal_event_watcher
            app.tmux_signal_event_watcher = None
        if watcher is not None:
            watcher.stop()

    def poll_watched_prs_client_event_once(self, app) -> list[str]:
        started = time.perf_counter()
        payload = app.watched_prs_payload()
        signature = app.client_event_payload_signature(payload)
        with self.state.lock:
            previous = self.state.watched_prs_signature
            self.state.watched_prs_signature = signature
        if not previous:
            return []
        if previous == signature:
            return []
        app.publish_client_event(
            "watched_prs_changed",
            {"data": payload},
            trigger="timer",
            cache="ready",
            compute_ms=(time.perf_counter() - started) * 1000,
        )
        return ["watched_prs_changed"]

    def start_client_event_watcher(self, app) -> None:
        now = time.monotonic()
        with self.state.lock:
            current = self.state.event_watcher_record
            if current.worker is not None and current.worker.is_alive():
                # A retained client-event worker must not make a previously failed tmux signal
                # watcher permanent. New SSE subscribers are the lifecycle re-entry point.
                app.start_tmux_signal_event_watcher()
                return
            record = ClientEventWatcherRecord(
                next_attention_ack_poll_at=now + app.server_attention_ack_event_poll_seconds(),
                next_tmux_signal_poll_at=now + app.server_tmux_signal_event_poll_seconds(),
            )
            worker = threading.Thread(target=app.client_event_watch_loop, args=(record,), name="client-event-watch", daemon=True)
            record.worker = worker
            self.state.event_watcher_record = record

        def rollback() -> None:
            owned = False
            with self.state.lock:
                if self.state.event_watcher_record is record and record.worker is worker:
                    record.stop_event.set()
                    record.watchd_stop_event.set()
                    record.wake_event.set()
                    self.state.event_watcher_record = ClientEventWatcherRecord()
                    owned = True
            if owned:
                app.stop_tmux_signal_event_watcher()

        try:
            app.start_tmux_signal_event_watcher()
        except Exception:
            rollback()
            raise
        common.start_thread_with_rollback(worker, rollback)
        try:
            app.start_watchd_revision_watcher(record)
        except RuntimeError as exc:
            # watchd owns both native watching and its polling fallback. The web
            # process reports a typed unavailable state and never scans locally.
            app.log_event(
                None,
                "watchd_error",
                f"watchd revision bridge failed to start: {exc}",
                {"diagnostic": str(exc)},
                message_key="events.message.clientEvent.directoryWatchFailed",
            )

    def stop_client_event_watcher(self, app) -> None:
        app.stop_tmux_signal_event_watcher()
        with self.state.lock:
            record = self.state.event_watcher_record
            record.stop_event.set()
            record.watchd_stop_event.set()
            record.wake_event.set()
            thread = record.worker
            watchd_worker = record.watchd_worker
            snapshot_worker = record.snapshot_worker
            record.snapshot_worker = None
        app.stop_status_generation_watcher(record)
        if snapshot_worker is not None:
            with app.activity_transcript_service.transcripts_payload_cache_lock:
                cache_record = app.activity_transcript_service.transcripts_payload_cache_record
                snapshot_generation = cache_record.generation if cache_record.worker is snapshot_worker else 0
            if snapshot_generation:
                app.finish_transcripts_payload_work(snapshot_generation, snapshot_worker, invalidate=True)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if watchd_worker is not None and watchd_worker is not threading.current_thread():
            watchd_worker.join(timeout=5.0)
        with self.state.lock:
            if self.state.event_watcher_record is record:
                self.state.event_watcher_record = ClientEventWatcherRecord()

    def stop_client_event_watcher_if_idle(self, app) -> bool:
        with app.client_events.lock:
            if app.client_events.subscribers:
                return False
        app.stop_client_event_watcher()
        return True

    def client_event_watch_loop(self, app, record: ClientEventWatcherRecord | None = None) -> None:
        current = record or self.state.event_watcher_record
        worker = threading.current_thread()
        try:
            while not current.stop_event.is_set():
                try:
                    now = time.monotonic()
                    status_demand = app.client_events.has_demand("status", "attention")
                    notification_demand = app.client_events.has_demand("attention")
                    if status_demand:
                        app.start_status_generation_watcher(current)
                    else:
                        app.stop_status_generation_watcher(current)
                    tmux_refresh_due = current.tmux_signal_refresh_at > 0 and now >= current.tmux_signal_refresh_at
                    tmux_fallback_due = not app.tmux_signal_event_watcher_healthy() and now >= current.next_tmux_signal_poll_at
                    if status_demand and (tmux_refresh_due or tmux_fallback_due):
                        events = app.poll_tmux_signals_client_event_once()
                        if tmux_fallback_due:
                            app.note_client_event_recurring_work(current, "tmux_signal_fallback", useful=bool(events))
                        current.tmux_signal_refresh_at = 0.0
                        if tmux_fallback_due:
                            current.next_tmux_signal_poll_at = now + app.server_tmux_signal_event_poll_seconds()
                    if (app.client_events.has_demand("core") or notification_demand) and now >= current.next_watched_pr_poll_at:
                        events = app.poll_watched_prs_client_event_once()
                        app.note_client_event_recurring_work(current, "watched_pr_reconcile", useful=bool(events))
                        current.next_watched_pr_poll_at = now + app.server_watched_pr_event_poll_seconds()
                    if (app.client_events.has_demand("yoagent") or notification_demand) and now >= current.next_yoagent_job_poll_at:
                        events = app.yoagent_controller.poll_yoagent_jobs_once()
                        app.note_client_event_recurring_work(current, "yoagent_job_reconcile", useful=bool(events))
                        current.next_yoagent_job_poll_at = now + YOAGENT_JOB_POLL_SECONDS
                    if (app.client_events.has_demand("core") or notification_demand) and now < app.search_progress_active_until and now >= current.next_search_progress_poll_at:
                        # Streaming Quick Open: while a crawl this process kicked is active and a palette
                        # client is subscribed, forward indexd's buffered progress frames onto the bus.
                        app.drain_and_publish_search_progress()
                        current.next_search_progress_poll_at = now + SEARCH_PROGRESS_DRAIN_POLL_SECONDS
                except (OSError, RuntimeError, ValueError) as exc:
                    app.log_event(
                        None,
                        "client_event_watch_error",
                        f"client event watch failed: {exc}",
                        {"diagnostic": str(exc)},
                        message_key="events.message.clientEvent.watchFailed",
                    )
                if current.wake_event.wait(app.client_event_watch_sleep_seconds(time.monotonic(), current)):
                    current.wake_event.clear()
        finally:
            app.stop_status_generation_watcher(current)
            with self.state.lock:
                if self.state.event_watcher_record is current and current.worker is worker:
                    current.worker = None


class OwnedStateAttribute:
    """Expose one composed owner's state field through the compatibility facade."""

    def __init__(self, owner_name: str, state_name: str) -> None:
        self.owner_name = owner_name
        self.state_name = state_name
        self.compatibility_name = f"__owned_state_{owner_name}_{state_name}"

    def __get__(self, instance: object, owner: type[object]) -> Any:
        if instance is None:
            return self
        composed = instance.__dict__.get(self.owner_name)
        if composed is None:
            return instance.__dict__.get(self.compatibility_name)
        return getattr(composed, self.state_name)

    def __set__(self, instance: object, value: Any) -> None:
        composed = instance.__dict__.get(self.owner_name)
        if composed is None:
            instance.__dict__[self.compatibility_name] = value
            return
        setattr(composed, self.state_name, value)

class SessionFilesCoordinator:
    """Own session-files caches, work records, publication, and operation lifecycle."""
    def __init__(self, app: "TmuxWebtermApp") -> None:
        self._app = app
        self.state = app.__dict__.pop("__owned_state__session_files_coordinator_state", None) or SessionFilesService()
    def start(self) -> None:
        self.state.allow_work()
    def stop(self) -> None:
        self.state.cancel_all_work()
    def cache_set_limited(self, app, cache: dict[Any, Any], key: Any, value: Any, limit: int) -> None:
        cache[key] = value
        while len(cache) > limit:
            cache.pop(next(iter(cache)))
    def session_files_exclusion_policy(self, app) -> exclusions.ExclusionPolicy:
        """Snapshot the configured Differ exclusion policy in the WEB process.

        Settings are read here and nowhere below: the snapshot is signed into the cache identity
        and shipped in the jobd task payload, so a worker judging paths can never be judging by a
        different policy than the one the cached answer is keyed on.
        """

        settings = app.settings_payload().get("settings", {}).get("file_explorer", {})
        return exclusions.ExclusionPolicy.from_settings(settings, session_files.DEFAULT_INDEX_EXCLUDE_DIR_NAMES)
    def session_files_cache_key( self, app, kind: str, infos: dict[str, SessionInfo], session: str | None, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> tuple[Any, ...]:
        repo_refs = session_files.canonical_repository_refs(repo_refs)
        repo_signatures: list[tuple[str, tuple[Any, ...]]] = []
        repo_roots = {
            session_files.canonical_repository_path(repo)
            for info in infos.values()
            for repo in session_files.session_candidate_repo_roots(info)
        }
        for repo_text in sorted(repo_roots):
            override = (repo_refs or {}).get(repo_text) or {}
            repo_from = str(override.get("from") or "").strip() or from_ref
            repo_to = str(override.get("to") or "").strip() or to_ref
            repo = Path(repo_text)
            # CRITICAL for "zero git spawns in warm requests": a `git status` identity ran here on
            # every key build after a repo change. When the fs watcher covers the repo, its dirty
            # generation int is the authoritative change signal, so use it and spawn NO git. The real
            # git identity/snapshot is recomputed inside the jobd worker on an actual recompute. When
            # the watcher is unhealthy, fall back to the git-spawn identity; the time-based
            # SESSION_FILES_CACHE_SECONDS staleness remains the no-watcher backstop.
            if app.watcher_covers_repo(repo):
                repo_signatures.append((repo_text, app.repo_dirty_generation(repo_text)))
            else:
                repo_signatures.append((repo_text, app.shared_git_identity(repo, repo_from, repo_to)[0]))
        return (
            kind,
            SESSION_FILES_CACHE_KEY_VERSION,
            session or "",
            session_files.bounded_session_files_hours(hours),
            str(from_ref or ""),
            str(to_ref or ""),
            repo_refs_cache_signature(repo_refs),
            # The exclusion policy decides which files the answer CONTAINS, so it belongs in the
            # identity of that answer. Without it, editing Preferences leaves every cached payload
            # -- memory, disk and the jobd coalesce key derived from this tuple -- serving rows the
            # new policy excludes.
            app.session_files_exclusion_policy().signature,
            tuple((name, session_info_cache_signature(info)) for name, info in sorted(infos.items())),
            tuple(repo_signatures),
        )
    def session_files_refresh_request_payload( self, app, cache_key: tuple[Any, ...], session: str | None, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> dict[str, Any]:
        return {
            "session": session or "",
            "hours": session_files.bounded_session_files_hours(hours),
            "from_ref": str(from_ref or ""),
            "to_ref": str(to_ref or ""),
            "repo_refs": repo_refs or {},
            "cache_key": repr(cache_key),
            "cache_key_data": cache_key,
        }
    def requested_session_files_cache_key( self, app, payload: dict[str, Any], fallback: tuple[Any, ...], ) -> tuple[Any, ...]:
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
    def repo_dirty_generation(self, app, repo_text: str) -> int:
        repo_text = session_files.canonical_repository_path(repo_text)
        with self.state.cache_lock:
            return self.state.repo_dirty_generations.setdefault(repo_text, 0)
    def mark_repo_state_dirty(self, app, changed_paths: list[Path]) -> None:
        with self.state.cache_lock:
            generations = self.state.repo_dirty_generations
            for repo_text in generations:
                repo_path = Path(repo_text)
                if any(
                    path == repo_path or filesystem._path_is_within(path, repo_path)
                    for path in changed_paths
                ):
                    generations[repo_text] += 1
    def store_git_identity(self, app, identity_key: tuple[Any, ...], dirty_generation: int, identity: tuple[Any, ...]) -> None:
        with self.state.cache_lock:
            self.state.repo_identity_cache[identity_key] = (dirty_generation, time.monotonic(), identity)
    def watcher_covers_repo(self, app, repo: Path) -> bool:
        """True when the native fs watcher is healthy AND watching a root that contains `repo`.

        This is the one predicate that decides whether a repo's dirty generation is authoritative,
        so both the cache-KEY path (which uses the generation int instead of spawning `git`) and the
        identity-reuse path (`reusable_git_identity`) share it rather than re-deriving the coverage
        test with divergent edge cases.
        """
        record = app.client_watch_service.event_watcher_record
        if not record.filesystem_healthy:
            return False
        resolved_repo = Path(str(repo)).expanduser().resolve(strict=False)
        return any(
            resolved_repo == Path(root) or filesystem._path_is_within(resolved_repo, Path(root))
            for root in record.filesystem_roots
        )
    def reusable_git_identity(self, app, identity_key: tuple[Any, ...], repo: Path) -> tuple[Any, ...] | None:
        if not app.watcher_covers_repo(repo):
            return None
        with self.state.cache_lock:
            entry = self.state.repo_identity_cache.get(identity_key)
            if entry is None:
                return None
            generation_at_compute, computed_at, identity = entry
            if generation_at_compute != self.state.repo_dirty_generations.get(identity_key[0], 0):
                return None
        if time.monotonic() - computed_at > app.SESSION_FILES_GIT_IDENTITY_SAFETY_SECONDS:
            return None
        return identity
    def session_files_disk_cache_path(self, app, key: tuple[Any, ...]) -> tuple[Path, str]: # Stable logical view identity only (kind, version, session, hours, refs, # per-repo ref overrides): the volatile info/repo signatures (key[-2:]) # are a replaceable source generation stored INSIDE the record, so agent # status or transcript appends REPLACE one durable file per view instead # of minting a new filename per generation.
        key_text = app.client_event_payload_signature(key[:-2])
        signature = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        return SESSION_FILES_CACHE_DIR / f"{signature}.json", signature
    def session_files_source_generation(self, app, key: tuple[Any, ...]) -> str:
        """The replaceable half of the cache identity: info + repo signatures."""
        return hashlib.sha256(app.client_event_payload_signature(key[-2:]).encode("utf-8")).hexdigest()
    def session_files_disk_manifest_path(self, app, signature: str) -> Path:
        return SESSION_FILES_CACHE_DIR / f"{signature}.manifest.json"
    def prune_session_files_disk_cache( self, app, *, max_age_seconds: float | None = None, max_bytes: int | None = None, now: float | None = None, ) -> dict[str, Any]:
        return session_files.prune_disk_cache(
            SESSION_FILES_CACHE_DIR,
            max_age_seconds=SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS if max_age_seconds is None else max_age_seconds,
            max_bytes=SESSION_FILES_DISK_CACHE_MAX_BYTES if max_bytes is None else max_bytes,
            batch_size=SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE,
            now=now,
        )
    def run_session_files_disk_cache_prune(self, app, record: SessionFilesDiskPruneRecord | None = None) -> None:
        active_record = record or self.state.disk_prune_record
        try:
            result = app.prune_session_files_disk_cache()
        except (OSError, RuntimeError, ValueError) as exc:
            result = {"error": str(exc)}
            logger.warning("session-files disk cache prune failed: %s", exc)
        with self.state.disk_prune_lock:
            if self.state.disk_prune_record is active_record:
                active_record.last_result = result
                active_record.running = False
                active_record.worker = None
        if result.get("removed_entries"):
            app.log_event(
                None,
                "session_files_cache_pruned",
                "Session-files disk cache pruned",
                result,
                message_key="events.message.sessionFiles.cachePruned",
            )
    def request_session_files_disk_cache_prune(self, app, reason: str = "") -> bool:
        now = time.monotonic()
        with self.state.disk_prune_lock:
            record = self.state.disk_prune_record
            if record.running or now < record.next_at:
                return False
            record.running = True
            record.next_at = now + SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS
        try:
            response, _body = app.job_client.produce(
                "session_files_cache_prune",
                {
                    "cache_dir": str(SESSION_FILES_CACHE_DIR),
                    "max_age_seconds": SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS,
                    "max_bytes": SESSION_FILES_DISK_CACHE_MAX_BYTES,
                    "batch_size": SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE,
                },
                priority="maintenance",
                generation=1,
                coalesce_key="session-files-cache-prune",
                delivery="receipt",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            response = {"ok": False, "error": str(exc)}
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        accepted = response.get("ok") is True and str(job.get("status") or "") in {"queued", "running", "completed"}
        with self.state.disk_prune_lock:
            if self.state.disk_prune_record is record:
                record.running = False
                record.worker = None
                record.last_result = {
                    "submitted": accepted,
                    "reason": reason,
                    "job_id": str(job.get("job_id") or ""),
                    **({"error": str(response.get("error") or "jobd did not accept session-files cache prune")} if not accepted else {}),
                }
        return accepted
    def session_files_payload_signature(self, app, payload: SessionFilesPayload | dict[str, Any]) -> str:
        payload_text = app.client_event_payload_signature(payload)
        return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    def set_session_files_memory_cache( self, app, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus, stored_at: float | None = None, ) -> None:
        with self.state.cache_lock:
            app.cache_set_limited(
                self.state.cache,
                key,
                (time.monotonic() if stored_at is None else stored_at, (copy.deepcopy(payload), status)),
                SESSION_FILES_CACHE_MAX_ITEMS,
            )
    def read_session_files_disk_cache( self, app, key: tuple[Any, ...], max_age_seconds: float | None = None, allow_stale: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
        path, signature = app.session_files_disk_cache_path(key)
        source_generation = app.session_files_source_generation(key)
        result = app.session_files_published_cache().read(
            path,
            signature,
            published_caches.SessionFilesFreshnessKey(source_generation),
            max_age_seconds=max_age_seconds,
            allow_stale=allow_stale,
        )
        if result is None:
            return None
        payload = result.payload.payload
        status = result.payload.status
        state = result.freshness
        # A source-mismatched record remains last-known-good only. Do not put it
        # under the requested in-memory key, where a strict read would see it.
        if state.current:
            app.set_session_files_memory_cache(key, payload, status, stored_at=time.monotonic() - state.age_seconds)
        return copy.deepcopy(payload), status, state.fresh, state.age_seconds
    def session_files_published_cache(self, app):
        return published_caches.session_files_cache(
            version=SESSION_FILES_CACHE_VERSION,
            cache_dir=SESSION_FILES_CACHE_DIR,
            payload_signature=app.session_files_payload_signature,
            owner_generation=lambda: app.background_owner.status_payload().get("generation", {}),
            record_phase=app.record_session_files_phase,
            request_prune=app.request_session_files_disk_cache_prune,
            clock=time.time,
            writer=atomic_write_text,
        )
    def write_session_files_disk_cache_unlocked( self, app, path: Path, signature: str, payload: SessionFilesPayload, status: HTTPStatus, source_generation: str = "", ) -> None:
        app.session_files_published_cache().write(
            path,
            signature,
            published_caches.SessionFilesCachedPayload(payload, status),
            published_caches.SessionFilesFreshnessKey(source_generation, status),
        )
    def write_session_files_disk_cache(self, app, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
        path, signature = app.session_files_disk_cache_path(key)
        try:
            with file_lock(path, dir_mode=0o700):
                app.write_session_files_disk_cache_unlocked(path, signature, payload, status, app.session_files_source_generation(key))
        except OSError as exc:
            logger.warning("failed to write session-files cache %s: %s", path, exc)
    def record_session_files_phase(self, app, phase: str, compute_ms: float, details: dict[str, Any]) -> None:
        app.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            f"phase:{str(phase or 'unknown')[:80]}",
            trigger="payload",
            compute_ms=compute_ms,
            cache_key={"kind": "session-files-phase"},
            cache_status="computed",
            details=details,
        )
    def shared_git_identity(self, app, repo: Path, from_ref: str | None, to_ref: str | None) -> tuple[tuple[Any, ...], str]:
        """One identity owner for BOTH the cache-key path and the snapshot path.

        Order of preference: watcher-cached (repository-state record unchanged
        -> ZERO Git commands), coalesced (another caller is computing it right
        now), computed. The future is removed once it completes, so sequential
        calls still recompute when the record is dirty and a file change is
        visible to the very next request — nothing is delayed or hidden.
        """
        identity_key = (str(repo), from_ref or "", to_ref or "")
        cached_identity = app.reusable_git_identity(identity_key, repo)
        if cached_identity is not None:
            return cached_identity, "watcher-cached"
        with self.state.cache_lock:
            identity_future = self.state.git_identity_futures.get(identity_key)
            identity_owner = identity_future is None
            if identity_owner:
                identity_future = Future()
                self.state.git_identity_futures[identity_key] = identity_future
        if not identity_owner:
            return identity_future.result(), "coalesced"
        try:
            dirty_generation_at_start = app.repo_dirty_generation(identity_key[0])
            snapshot_identity = session_files.git_snapshot_identity(repo, from_ref, to_ref)
            app.store_git_identity(identity_key, dirty_generation_at_start, snapshot_identity)
            identity_future.set_result(snapshot_identity)
        except BaseException as error:
            identity_future.set_exception(error)
            raise
        finally:
            with self.state.cache_lock:
                self.state.git_identity_futures.pop(identity_key, None)
        return snapshot_identity, "computed"
    def shared_session_files_git_snapshot( self, app, repo: Path, from_ref: str | None, to_ref: str | None, *, identity: tuple[Any, ...] | None = None, ) -> dict[str, Any]:
        signature_started = time.perf_counter()
        if identity is not None:
            snapshot_identity, identity_status = identity, "provided"
        else:
            snapshot_identity, identity_status = app.shared_git_identity(repo, from_ref, to_ref)
        app.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            "phase:git-signature",
            trigger="payload",
            compute_ms=(time.perf_counter() - signature_started) * 1000,
            cache_key={"kind": "git-snapshot"},
            cache_status=identity_status,
            cache_hit=identity_status != "computed",
            details={"repo": str(repo)},
        )
        with self.state.cache_lock:
            record = self.state.git_snapshot_records.get(snapshot_identity)
            if record is not None and record.snapshot is not None:
                app.record_performance_sample(
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
                self.state.git_snapshot_records[snapshot_identity] = record
                owner = True
            else:
                owner = False
        if not owner:
            final_identity, snapshot = record.future.result()
            app.record_performance_sample(
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
                return app.shared_session_files_git_snapshot(repo, from_ref, to_ref)
            return copy.deepcopy(snapshot)
        started = time.perf_counter()
        try:
            snapshot = session_files.build_git_snapshot(repo, from_ref, to_ref)
            final_identity = session_files.git_snapshot_identity(repo, from_ref, to_ref)
            compute_ms = (time.perf_counter() - started) * 1000
            app.record_performance_sample(
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
            with self.state.cache_lock:
                if final_identity == snapshot_identity:
                    record.snapshot = copy.deepcopy(snapshot)
                    while len(self.state.git_snapshot_records) > SESSION_FILES_GIT_SNAPSHOT_MAX_ITEMS:
                        oldest_key = next(iter(self.state.git_snapshot_records))
                        if oldest_key == snapshot_identity and len(self.state.git_snapshot_records) > 1:
                            oldest_key = next(key for key in self.state.git_snapshot_records if key != snapshot_identity)
                        self.state.git_snapshot_records.pop(oldest_key, None)
                elif self.state.git_snapshot_records.get(snapshot_identity) is record:
                    self.state.git_snapshot_records.pop(snapshot_identity, None)
            if final_identity != snapshot_identity:
                return app.shared_session_files_git_snapshot(repo, from_ref, to_ref)
            return copy.deepcopy(snapshot)
        except Exception as exc:
            if not record.future.done():
                record.future.set_exception(exc)
            with self.state.cache_lock:
                if self.state.git_snapshot_records.get(snapshot_identity) is record:
                    self.state.git_snapshot_records.pop(snapshot_identity, None)
            raise
    def complete_session_files_work( self, app, key: tuple[Any, ...], record: SessionFilesWorkRecord, result: tuple[SessionFilesPayload, HTTPStatus, bool, float] | None = None, error: Exception | None = None, ) -> None:
        if error is not None and not record.future.done():
            record.future.set_exception(error)
        elif result is not None and not record.future.done():
            record.future.set_result((copy.deepcopy(result[0]), result[1], result[2], result[3]))
        self.state.finish_work(key, record)
    def compute_session_files_cache_entry( self, app, key: tuple[Any, ...], compute: Callable[[], tuple[SessionFilesPayload, HTTPStatus]], *, reserved: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float]:
        path, signature = app.session_files_disk_cache_path(key)
        work_record, owner = self.state.claim_work(
            key,
            threading.get_ident(),
            reserved=reserved,
            stable_signature=signature,
        )
        if not owner:
            payload, status, cache_hit, age_seconds = work_record.future.result()
            app.record_performance_sample(
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
                cached = app.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=False)
                if cached:
                    payload, status, _fresh, age_seconds = cached
                    app.record_performance_sample(
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
                    app.complete_session_files_work(key, work_record, result=result)
                    return result
                # Only a true cache miss enters the owner-wide queue.  Hits and
                # followers remain cheap, while unrelated HTTP handlers never
                # contend for these transcript/Git slots.
                queue_started = time.perf_counter()
                self.state.acquire_compute_slot(app.session_files_max_workers())
                compute_slot_acquired = True
                app.record_session_files_phase(
                    "cold-rebuild-queue",
                    (time.perf_counter() - queue_started) * 1000,
                    {"cache_key_kind": app.performance_cache_key_kind(key)},
                )
                compute_attempted = True
                payload, status = compute()
                computed_result = (payload, status)
                serialization_started = time.perf_counter()
                if self.state.stable_generation_is_current(work_record):
                    app.set_session_files_memory_cache(key, payload, status)
                    app.write_session_files_disk_cache_unlocked(path, signature, payload, status, app.session_files_source_generation(key))
                app.record_session_files_phase(
                    "cache-serialization",
                    (time.perf_counter() - serialization_started) * 1000,
                    {
                        "cache_key_kind": app.performance_cache_key_kind(key),
                        "payload_bytes": app.performance_payload_bytes(payload),
                        # Cumulative work counters (git spawns per verb, catalog
                        # traversal, append bytes, untracked stat/read work) so
                        # per-build deltas are derivable from consecutive samples.
                        "runtime_counters": session_files.session_files_runtime_counters(),
                    },
                )
                app.record_performance_sample(
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
                app.complete_session_files_work(key, work_record, result=result)
                return result
        except OSError as exc:
            logger.warning("failed to lock session-files cache %s: %s", path, exc)
            if compute_attempted:
                if computed_result is None:
                    app.complete_session_files_work(key, work_record, error=exc)
                    raise
                payload, status = computed_result
                result = (copy.deepcopy(payload), status, False, 0.0)
                app.complete_session_files_work(key, work_record, result=result)
                return result
            try:
                queue_started = time.perf_counter()
                self.state.acquire_compute_slot(app.session_files_max_workers())
                compute_slot_acquired = True
                app.record_session_files_phase(
                    "cold-rebuild-queue",
                    (time.perf_counter() - queue_started) * 1000,
                    {"cache_key_kind": app.performance_cache_key_kind(key), "lock_fallback": True},
                )
                compute_attempted = True
                payload, status = compute()
            except Exception as compute_exc:
                app.complete_session_files_work(key, work_record, error=compute_exc)
                raise
            app.set_session_files_memory_cache(key, payload, status)
            app.record_performance_sample(
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
            app.complete_session_files_work(key, work_record, result=result)
            return result
        except Exception as exc:
            app.complete_session_files_work(key, work_record, error=exc)
            raise
        finally:
            if compute_slot_acquired:
                self.state.release_compute_slot()
    def get_session_files_cache( self, app, key: tuple[Any, ...], max_age_seconds: float | None = None, allow_stale: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
        started = time.perf_counter()
        now = time.monotonic()
        stale_cached: tuple[SessionFilesPayload, HTTPStatus, bool, float] | None = None
        with self.state.cache_lock:
            cached = self.state.cache.get(key)
            if cached:
                stored_at, value = cached
                age_seconds = max(0.0, now - stored_at)
                fresh = max_age_seconds is None or age_seconds <= max_age_seconds
                payload, status = value
                if fresh:
                    app.record_performance_sample(
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
        disk_cached = app.read_session_files_disk_cache(key, max_age_seconds=max_age_seconds, allow_stale=allow_stale)
        if disk_cached:
            if stale_cached is None or disk_cached[3] <= stale_cached[3]:
                app.record_performance_sample(
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
            app.record_performance_sample(
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
        app.record_performance_sample(
            BACKGROUND_ROLE_SESSION_FILES,
            "cache-read",
            trigger="miss",
            compute_ms=(time.perf_counter() - started) * 1000,
            cache_key=key,
            cache_status="miss",
            cache_hit=False,
        )
        return None
    def set_session_files_cache(self, app, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
        app.set_session_files_memory_cache(key, payload, status)
        app.write_session_files_disk_cache(key, payload, status)
    def clear_session_files_cache(self, app) -> None:
        with self.state.cache_lock:
            self.state.cache.clear()
            self.state.git_snapshot_records.clear()
    def session_files_git_identity_for_cache_key(self, app, cache_key: tuple[Any, ...] | None, repo: Path) -> tuple[Any, ...] | None:
        if not cache_key or not isinstance(cache_key[-1], tuple):
            return None
        canonical_repo = session_files.canonical_repository_path(repo)
        for item in cache_key[-1]:
            if not isinstance(item, tuple) or len(item) != 2 or str(item[0]) != canonical_repo:
                continue
            return item[1] if isinstance(item[1], tuple) else None
        return None
    def session_files_git_snapshot_provider(self, app, cache_key: tuple[Any, ...] | None) -> Callable[[Path, str | None, str | None], dict[str, Any]]:
        def provider(repo: Path, repo_from: str | None, repo_to: str | None) -> dict[str, Any]:
            return app.shared_session_files_git_snapshot(repo, repo_from, repo_to, identity=app.session_files_git_identity_for_cache_key(cache_key, repo))
        return provider
    def compute_session_files_payload_for_info( self, app, info: SessionInfo, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...] | None = None, ) -> SessionFilesPayload:
        return session_files.session_files_payload_for_info(
            info,
            hours=hours,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            git_snapshot_provider=app.session_files_git_snapshot_provider(cache_key),
            phase_recorder=app.record_session_files_phase,
        )
    def session_files_view_coalesce_identity(self, app, cache_key: tuple[Any, ...]) -> tuple[str, int]:
        """Cross-port product identity for `session_files_view`.

        The coalesce_key is the stable view signature plus the replaceable info+repo source
        generation, so two web ports sharing one jobd socket and one disk-cache dir dedupe to ONE
        worker execution for the same product. The numeric generation is derived from that same
        source signature and drives jobd's generation guard, so an older completion can never
        overwrite a newer product for the same view.
        """
        _path, signature = app.session_files_disk_cache_path(cache_key)
        source_generation = app.session_files_source_generation(cache_key)
        coalesce_key = f"session_files:{signature}:{source_generation}"[:256]
        generation = int(hashlib.sha256(source_generation.encode("utf-8")).hexdigest()[:12], 16)
        return coalesce_key, generation
    def session_files_jobd_source_profile(self, app, cache_key: tuple[Any, ...], requester: str) -> dict[str, str | int]:
        """Return bounded source-change facts for jobd diagnostics, never raw cache-key contents."""
        _path, stable_view = app.session_files_disk_cache_path(cache_key)
        repo_generations = [item[1] for item in cache_key[-1] if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], int)]
        return {
            "requester": requester,
            "stable_view": stable_view,
            "info_signature": hashlib.sha256(app.client_event_payload_signature(cache_key[-2]).encode("utf-8")).hexdigest(),
            "repo_signature": hashlib.sha256(app.client_event_payload_signature(cache_key[-1]).encode("utf-8")).hexdigest(),
            "repo_dirty_generation_count": len(repo_generations),
            "repo_dirty_generation_max": max(repo_generations, default=0),
        }
    @staticmethod
    def session_files_jobd_repository_states(cache_key: tuple[Any, ...]) -> list[dict[str, object]]:
        """Pass only watcher-authoritative repository generations to jobd's Git-facts cache."""
        states = []
        for item in cache_key[-1]:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], int):
                states.append({"path": session_files.canonical_repository_path(item[0]), "generation": item[1]})
        return states
    def submit_session_files_job( self, app, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...], *, priority: str = "freshness", requester: str = "unknown", ) -> tuple[dict[str, Any], str, int]:
        """Submit one immutable session-files job and return its atomic broker receipt."""
        coalesce_key, generation = app.session_files_view_coalesce_identity(cache_key)
        payload = {
            "session": session or "",
            "infos": {name: asdict(info) for name, info in infos.items()},
            "hours": session_files.bounded_session_files_hours(hours),
            "from_ref": str(from_ref or ""),
            "to_ref": str(to_ref or ""),
            "repo_refs": repo_refs or {},
            "include_cross_session_attribution": not bool(session),
            "source": app.session_files_jobd_source_profile(cache_key, requester),
            "repository_states": app.session_files_jobd_repository_states(cache_key),
            # Serializable policy, not a lookup: the worker applies exactly this at both doors.
            "exclusion_policy": app.session_files_exclusion_policy().as_payload(),
        }
        response = app.job_client.submit(
            "session_files_view",
            payload,
            priority=priority,
            generation=generation,
            coalesce_key=coalesce_key,
            deadline_ms=SESSION_FILES_JOBD_JOB_DEADLINE_MS,
        )
        return response, coalesce_key, generation
    def compute_session_files_payload_via_jobd( self, app, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...], *, priority: str = "freshness", requester: str = "unknown", ) -> tuple[SessionFilesPayload, HTTPStatus]:
        """Materialize a session-files payload in the background cache worker."""
        response, coalesce_key, generation = app.submit_session_files_job(
            session,
            infos,
            hours,
            from_ref,
            to_ref,
            repo_refs,
            cache_key,
            priority=priority,
            requester=requester,
        )
        if not response.get("ok"):
            raise SessionFilesJobdUnavailable(
                str(response.get("error") or "jobd submit rejected"),
                response,
            )
        try:
            _meta, body, state = wait_for_jobd_product(
                app.job_client, coalesce_key, generation, SESSION_FILES_JOBD_WAIT_SECONDS
            )
        except JobdProductRpcUnavailable as error:
            raise SessionFilesJobdUnavailable(str(error)) from error
        if body is None:
            raise SessionFilesJobdUnavailable(f"jobd product not ready (state={state or 'none'})")
        return app.session_files_payload_from_product(body)
    def session_files_payload_from_product(self, app, body: bytes) -> tuple[SessionFilesPayload, HTTPStatus]:
        data = json.loads(body.decode("utf-8"))
        payload = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            raise SessionFilesJobdUnavailable("malformed jobd session-files product")
        status = HTTPStatus(int(data.get("status") or int(HTTPStatus.OK)))
        return payload, status
    def session_files_payload_from_job(self, app, job: dict[str, Any]) -> tuple[SessionFilesPayload, HTTPStatus]:
        result = job.get("result")
        payload = result.get("payload") if isinstance(result, dict) else None
        if not isinstance(payload, dict):
            raise SessionFilesJobdUnavailable(
                "malformed jobd session-files result",
                {"error": "malformed jobd session-files result", "status": str(job.get("status") or "")},
            )
        status = HTTPStatus(int(result.get("status") or int(HTTPStatus.OK)))
        return payload, status
    def wait_for_session_files_operation_job( self, app, job_id: str, deadline_at: float, ) -> tuple[SessionFilesPayload, HTTPStatus]:
        """Wait only in the accepted-operation worker; HTTP returns before this loop starts."""
        try:
            job = app.wait_for_jobd_operation_job(job_id, deadline_at)
        except JobdOperationUnavailable as error:
            raise SessionFilesJobdUnavailable(str(error), error.failure) from error
        return app.session_files_payload_from_job(job)
    def complete_session_files_operation( self, app, operation_id: str, request_id: str, job_id: str, cache_key: tuple[Any, ...], deadline_at: float, ) -> None:
        """Supervisor boundary for one accepted producer and its terminal delivery."""
        try:
            payload, status, cache_hit, age_seconds = app.compute_session_files_cache_entry(
                cache_key,
                lambda: app.wait_for_session_files_operation_job(job_id, deadline_at),
            )
            terminal_payload = copy.deepcopy(payload)
            terminal_payload["cache"] = {
                "hit": bool(cache_hit),
                "stale": False,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": SESSION_FILES_CACHE_SECONDS,
            }
            result = app.session_files_ready_result(request_id, terminal_payload)
            app.terminalize_operation(operation_id, result, status)
        except SessionFilesJobdUnavailable as error:
            result = app.session_files_failure_result(
                request_id,
                error.failure or {"error": str(error)},
                operation_id=operation_id,
                operation="jobd.result",
            )
            app.terminalize_operation(operation_id, result, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as error:
            result = app.session_files_failure_result(
                request_id,
                {
                    "error": str(error),
                    "cause": local_service_exception_cause(error),
                },
                operation_id=operation_id,
                operation="session-files.complete",
                code="producer_failed",
            )
            app.terminalize_operation(operation_id, result, HTTPStatus.INTERNAL_SERVER_ERROR)
    def start_session_files_operation( self, app, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...], *, priority: str, requester: str, ) -> tuple[dict[str, Any], HTTPStatus]:
        request_id = app.new_api_request_id()
        response, coalesce_key, generation = app.submit_session_files_job(
            session,
            infos,
            hours,
            from_ref,
            to_ref,
            repo_refs,
            cache_key,
            priority=priority,
            requester=requester,
        )
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        job_id = str(job.get("job_id") or "")
        producer_state = str(job.get("status") or "")
        if not response.get("ok") or not job_id or producer_state not in {"queued", "running", "completed"}:
            failure = dict(response)
            if not failure.get("error"):
                failure["error"] = "jobd did not return an accepted job receipt"
            result = app.session_files_failure_result(
                request_id,
                failure,
                operation="jobd.submit",
            )
            app.record_operation_failure("", result)
            return result, HTTPStatus.SERVICE_UNAVAILABLE
        reservation = app.jobd_operation_service.reserve("bulk")
        if reservation is None:
            result = app.session_files_failure_result(
                request_id,
                {"error": "jobd operation completion pool is full", "status": "service_busy"},
                operation="jobd.submit",
                code="service_busy",
            )
            app.record_operation_failure("", result)
            return result, HTTPStatus.SERVICE_UNAVAILABLE
        deadline_at = time.time() + (SESSION_FILES_JOBD_JOB_DEADLINE_MS / 1000.0)
        try:
            receipt = app.queued_delivery_ledger.accept_operation(
                request_id=request_id,
                route="GET /api/session-files",
                deadline_at=deadline_at,
                progress={
                    "phase": "waiting_for_product",
                    "producer": "jobd",
                    "producer_state": producer_state,
                },
                producer={
                    "service": "jobd",
                    "job_id": job_id,
                    "coalesce_key": coalesce_key,
                    "generation": generation,
                },
                kind="session_files",
                context={
                    "session": str(session or ""),
                    "from_ref": str(from_ref or ""),
                    "to_ref": str(to_ref or ""),
                    "hours": float(hours),
                    "repo_refs": session_files.canonical_repository_refs(repo_refs),
                },
            )
        except Exception:
            reservation.release()
            raise
        operation_id = str(receipt["operation"]["id"])
        submitted = app.jobd_operation_service.submit_reserved(
            reservation,
            app.complete_session_files_operation,
            operation_id,
            request_id,
            job_id,
            cache_key,
            deadline_at,
        )
        if not submitted:
            result = app.session_files_failure_result(
                request_id,
                {"error": "jobd operation completion worker could not start"},
                operation_id=operation_id,
                operation="session-files.start",
                code="producer_failed",
            )
            app.terminalize_operation(operation_id, result, HTTPStatus.INTERNAL_SERVER_ERROR)
            return result, HTTPStatus.INTERNAL_SERVER_ERROR
        return receipt, HTTPStatus.ACCEPTED
    def refresh_session_files_payload_cache( self, app, cache_key: tuple[Any, ...], session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> None:
        started = time.perf_counter()
        refresh_details = app.background_refresh_event_details(BACKGROUND_ROLE_SESSION_FILES, {"session": session or ""}, cache_key=cache_key)
        app.log_sampled_background_refresh_event(
            "background_refresh_started",
            BACKGROUND_ROLE_SESSION_FILES,
            "Session-files background refresh started",
            refresh_details,
            message_key="events.message.backgroundRefresh.started",
            message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
        )
        try:
            app.compute_session_files_cache_entry(
                cache_key,
                lambda: app.compute_session_files_payload_via_jobd(session, infos, hours, from_ref, to_ref, repo_refs, cache_key, requester="background-refresh"),
                reserved=True,
            )
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            app.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh finished",
                done_details,
                message_key="events.message.backgroundRefresh.finished",
                message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
            )
            app.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {**refresh_details, "compute_ms": compute_ms})
        except SessionFilesJobdUnavailable as exc:
            # jobd could not produce the product this cycle. The single-flight is already released by
            # compute_session_files_cache_entry; nothing stale is cached and the next request retries.
            logger.info("session-files payload refresh deferred (jobd) for %s: %s", cache_key, exc)
        except Exception as exc:
            logger.warning("session-files payload refresh failed for %s: %s", cache_key, exc)
            raise
    def refresh_session_files_info_cache( self, app, cache_key: tuple[Any, ...], info: SessionInfo, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> None:
        started = time.perf_counter()
        refresh_details = app.background_refresh_event_details(BACKGROUND_ROLE_SESSION_FILES, {"session": info.session}, cache_key=cache_key)
        app.log_sampled_background_refresh_event(
            "background_refresh_started",
            BACKGROUND_ROLE_SESSION_FILES,
            "Session-files background refresh started",
            refresh_details,
            message_key="events.message.backgroundRefresh.started",
            message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
        )
        try:
            app.compute_session_files_cache_entry(
                cache_key,
                lambda: app.compute_session_files_payload_via_jobd(info.session, {info.session: info}, hours, from_ref, to_ref, repo_refs, cache_key, requester="background-info-refresh"),
                reserved=True,
            )
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            app.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh finished",
                done_details,
                message_key="events.message.backgroundRefresh.finished",
                message_params={"target": message_descriptor("backgroundOwner.sessionFiles", "Session files")},
            )
            app.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {**refresh_details, "compute_ms": compute_ms})
        except SessionFilesJobdUnavailable as exc:
            logger.info("session-files info refresh deferred (jobd) for %s: %s", cache_key, exc)
        except Exception as exc:
            logger.warning("session-files info refresh failed for %s: %s", cache_key, exc)
            raise
    def start_session_files_cache_refresh(self, app, cache_key: tuple[Any, ...], target: Any, *args: Any) -> bool:
        if not app.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            if target == app.refresh_session_files_payload_cache and len(args) >= 6:
                session, _infos, hours, from_ref, to_ref, repo_refs = args[:6]
                request_payload = app.session_files_refresh_request_payload(cache_key, session, hours, from_ref, to_ref, repo_refs)
            else:
                request_payload = {"cache_key": repr(cache_key), "cache_key_data": cache_key}
            app.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, request_payload)
            return False
        _path, stable_signature = app.session_files_disk_cache_path(cache_key)
        record = self.state.reserve_work(cache_key, stable_signature)
        if record is None:
            return False
        def run_reserved_worker() -> None:
            try:
                target(cache_key, *args)
            except BaseException as exc:
                if not record.future.done():
                    record.future.set_exception(exc)
                raise
            finally:
                self.state.finish_reserved_worker(cache_key, record, threading.current_thread())

        worker = threading.Thread(target=run_reserved_worker, daemon=True)
        try:
            if not self.state.start_reserved_worker(cache_key, record, worker):
                return False
        except RuntimeError as exc:
            if not record.future.done():
                record.future.set_exception(exc)
            raise
        return True
    def start_requested_session_files_cache_refresh(self, app, payload: dict[str, Any]) -> bool:
        session = str(payload.get("session") or "").strip()
        scope = [session] if session else list(app.sessions)
        infos, _errors = discover_sessions(scope)
        if session and session not in infos:
            return False
        hours = session_files.bounded_session_files_hours(app.float_value(payload.get("hours"), 24.0))
        from_ref = str(payload.get("from_ref") or "").strip() or None
        to_ref = str(payload.get("to_ref") or "").strip() or None
        raw_repo_refs = payload.get("repo_refs")
        repo_refs = raw_repo_refs if isinstance(raw_repo_refs, dict) else {}
        fallback_key = app.session_files_cache_key("payload", infos, session or None, hours, from_ref, to_ref, repo_refs)
        cache_key = app.requested_session_files_cache_key(payload, fallback_key)
        return app.start_session_files_cache_refresh(
            cache_key,
            app.refresh_session_files_payload_cache,
            session or None,
            infos,
            hours,
            from_ref,
            to_ref,
            repo_refs,
        )
    def cached_session_files_payload_for_info( self, app, info: SessionInfo, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, *, wait_for_fresh: bool = True, ) -> SessionFilesPayload:
        infos = {info.session: info}
        key = app.session_files_cache_key("payload", infos, info.session, hours, from_ref, to_ref, repo_refs)
        cached = app.get_session_files_cache(key, max_age_seconds=SESSION_FILES_CACHE_SECONDS, allow_stale=True)
        if cached:
            payload, _status, fresh, _age = cached
            if not fresh:
                if app.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
                    app.start_session_files_cache_refresh(key, app.refresh_session_files_info_cache, info, hours, from_ref, to_ref, repo_refs)
                else:
                    app.record_background_follower_stale_read(BACKGROUND_ROLE_SESSION_FILES)
                    refresh_result = app.request_background_refresh(
                        BACKGROUND_ROLE_SESSION_FILES,
                        app.session_files_refresh_request_payload(key, info.session, hours, from_ref, to_ref, repo_refs),
                    )
                    app.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                    if app.background_refresh_should_fallback(refresh_result):
                        try:
                            payload, _status, _hit, _age = app.compute_session_files_cache_entry(
                                key,
                                lambda: app.compute_session_files_payload_via_jobd(info.session, infos, hours, from_ref, to_ref, repo_refs, key, priority="interactive", requester="metadata-follower-fallback"),
                            )
                        except SessionFilesJobdUnavailable:
                            # Serve the stale bytes already read above; never resurrect inline git here.
                            pass
            return payload
        if not wait_for_fresh:
            app.start_session_files_cache_refresh(
                key,
                app.refresh_session_files_info_cache,
                info,
                hours,
                from_ref,
                to_ref,
                repo_refs,
            )
            return {
                "session": info.session,
                "hours": session_files.bounded_session_files_hours(hours),
                "files": [],
                "repos": [],
                "errors": [],
                "refreshing_elsewhere": True,
            }
        if not app.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            refresh_result = app.request_background_refresh(
                BACKGROUND_ROLE_SESSION_FILES,
                app.session_files_refresh_request_payload(key, info.session, hours, from_ref, to_ref, repo_refs),
            )
            app.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
            if app.background_refresh_should_fallback(refresh_result):
                try:
                    payload, _status, _hit, _age = app.compute_session_files_cache_entry(
                        key,
                        lambda: app.compute_session_files_payload_via_jobd(info.session, infos, hours, from_ref, to_ref, repo_refs, key, priority="interactive", requester="metadata-follower-fallback"),
                    )
                    return copy.deepcopy(payload)
                except SessionFilesJobdUnavailable:
                    pass
            return {"files": [], "repos": [], "errors": [], "refreshing_elsewhere": True}
        try:
            payload, _status, _hit, _age = app.compute_session_files_cache_entry(
                key,
                lambda: app.compute_session_files_payload_via_jobd(info.session, infos, hours, from_ref, to_ref, repo_refs, key, priority="interactive", requester="metadata-cache-miss"),
            )
            return copy.deepcopy(payload)
        except SessionFilesJobdUnavailable:
            return {"files": [], "repos": [], "errors": [], "refreshing_elsewhere": True}
    def warm_start_session_files_payload_cache(self, app) -> None:
        if not app.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            app.request_background_refresh(BACKGROUND_ROLE_SESSION_FILES, {"reason": "warm-start"})
            return
        sessions, _errors = discover_sessions(app.sessions)
        for session in app.sessions:
            info = sessions.get(session)
            if info is not None and info.agents:
                key = app.session_files_cache_key("payload", {session: info}, session, 24.0, None, None, None)
                app.get_session_files_cache(key, max_age_seconds=None, allow_stale=True)
    def warm_start_tabber_activity_cache(self, app) -> None:
        if not app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            app.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "warm-start"})
            return
        source_signature = app.tabber_activity_source_signature()
        app.get_tabber_activity_cache(float("inf"), allow_stale=True, hours=24.0, source_signature=source_signature)
    def cached_session_files_payloads_for_infos( self, app, infos: dict[str, SessionInfo], hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, ) -> dict[str, SessionFilesPayload]:
        if not infos:
            return {}
        if len(infos) == 1:
            session, info = next(iter(infos.items()))
            return {session: app.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)}
        if not app.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
            return {
                session: app.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
                for session, info in infos.items()
            }
        payloads: dict[str, SessionFilesPayload] = {}
        for session, info in infos.items():
            payloads[session] = app.cached_session_files_payload_for_info(info, hours=hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
        return payloads
    def session_files_payload_for_infos( self, app, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, requester: str = "api-session-files", extra_errors: list[str | dict[str, Any]] | None = None, accepted_operation: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus]:
        started = time.perf_counter()
        cache_key = app.session_files_cache_key("payload", infos, session, hours, from_ref, to_ref, repo_refs)
        max_age = SESSION_FILES_CACHE_SECONDS
        cached = None if force else app.get_session_files_cache(cache_key, max_age_seconds=max_age, allow_stale=True)
        priority = "interactive" if force else "freshness"

        def compute_via_jobd() -> tuple[SessionFilesPayload, HTTPStatus]:
            app.client_watch_service.note_owner_invocation("session_files_materialization")
            return app.compute_session_files_payload_via_jobd(
                session,
                infos,
                hours,
                from_ref,
                to_ref,
                repo_refs,
                cache_key,
                priority=priority,
                requester=requester,
            )

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
                if app.background_can_run(BACKGROUND_ROLE_SESSION_FILES):
                    refreshing = app.start_session_files_cache_refresh(cache_key, app.refresh_session_files_payload_cache, session, infos, hours, from_ref, to_ref, repo_refs)
                    cache_meta["refreshing"] = refreshing
                else:
                    app.record_background_follower_stale_read(BACKGROUND_ROLE_SESSION_FILES)
                    refresh_result = app.request_background_refresh(
                        BACKGROUND_ROLE_SESSION_FILES,
                        app.session_files_refresh_request_payload(cache_key, session, hours, from_ref, to_ref, repo_refs),
                    )
                    app.record_background_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
                    if app.background_refresh_should_fallback(refresh_result):
                        try:
                            payload, status, cache_hit, age_seconds = app.compute_session_files_cache_entry(cache_key, compute_via_jobd)
                            cache_meta = {
                                "hit": cache_hit,
                                "stale": False,
                                "age_seconds": round(age_seconds, 3),
                                "refresh_seconds": max_age,
                                "fallback": True,
                            }
                        except SessionFilesJobdUnavailable:
                            cache_meta["refreshing_elsewhere"] = True
                    else:
                        cache_meta["refreshing_elsewhere"] = True
        else:
            if accepted_operation:
                payload, status = app.start_session_files_operation(
                    session,
                    infos,
                    hours,
                    from_ref,
                    to_ref,
                    repo_refs,
                    cache_key,
                    priority=priority,
                    requester=requester,
                )
                cache_meta = {
                    "hit": False,
                    "stale": False,
                    "refreshing_elsewhere": status == HTTPStatus.ACCEPTED,
                }
            else:
                try:
                    payload, status, cache_hit, age_seconds = app.compute_session_files_cache_entry(cache_key, compute_via_jobd)
                    cache_meta = {
                        "hit": cache_hit,
                        "stale": False,
                        "age_seconds": round(age_seconds, 3),
                        "refresh_seconds": max_age,
                        "refreshing": False,
                    }
                except SessionFilesJobdUnavailable as error:
                    payload = {"ok": False, "status": "SERVICE_UNAVAILABLE", "reason": str(error), "terminal": True}
                    status = HTTPStatus.SERVICE_UNAVAILABLE
                    cache_meta = {"hit": False, "stale": False, "refreshing_elsewhere": False}
        payload = copy.deepcopy(payload)
        if status == HTTPStatus.ACCEPTED:
            app.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "payload",
                trigger="force" if force else "request",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=payload,
                cache_key=cache_key,
                cache_status="refreshing-elsewhere",
                cache_hit=False,
                cache_fresh=False,
                details={"session": session or "", "status": int(status)},
            )
            return payload, status
        if status >= HTTPStatus.BAD_REQUEST and payload.get("state") == "failed":
            app.record_performance_sample(
                BACKGROUND_ROLE_SESSION_FILES,
                "payload",
                trigger="force" if force else "request",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=payload,
                cache_key=cache_key,
                cache_status=str(int(status)),
                cache_hit=False,
                cache_fresh=False,
                details={"session": session or "", "status": int(status)},
            )
            return payload, status
        structured_extra_errors = [
            value if isinstance(value, dict) else message_descriptor("diff.warning.discovery", value, {"error": value})
            for value in (extra_errors or [])
        ]
        payload["errors"] = [*structured_extra_errors, *payload.get("errors", [])]
        payload["cache"] = cache_meta
        app.record_performance_sample(
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
    def session_files_payload( self, app, session: str | None = None, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, requester: str = "api-session-files", accepted_operation: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus]:
        refresh_errors = app.refresh_sessions()
        if session and session not in app.sessions:
            diagnostic = f"unknown session: {session}"
            return {"session": session, **user_message_payload("status.sessionEnded", diagnostic, session=session)}, HTTPStatus.NOT_FOUND
        scope = [session] if session else app.sessions
        infos, errors = discover_sessions(scope)
        return app.session_files_payload_for_infos(
            session,
            infos,
            hours,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            force=force,
            requester=requester,
            extra_errors=[*refresh_errors, *errors],
            accepted_operation=accepted_operation,
        )
    def session_files_http_payload( self, app, session: str | None = None, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, ) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = app.session_files_payload(
            session,
            hours,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            force=force,
            accepted_operation=True,
        )
        if payload.get("state") in {"queued", "failed"}:
            return payload, status
        request_id = app.new_api_request_id()
        if status < HTTPStatus.BAD_REQUEST:
            return app.session_files_ready_result(request_id, payload), status
        descriptor = payload.get("user_message") if isinstance(payload.get("user_message"), dict) else {}
        message = str(descriptor.get("fallback") or payload.get("error") or "session-files request failed")
        return common.error_payload(
            message,
            message_key=str(descriptor.get("key") or "common.requestFailed"),
            message_params=descriptor.get("params") if isinstance(descriptor.get("params"), dict) else {},
            canonical=True,
            code="session_files_request_failed",
            origin="server.http",
            retryable=False,
            details={"session": str(session or ""), "status": int(status)},
            stack=[{
                "component": "server.http",
                "operation": "GET /api/session-files",
                "code": "session_files_request_failed",
            }],
            request_id=request_id,
        ), status
    def session_files_batch_payload( self, app, sessions: list[str] | None = None, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, ) -> tuple[dict[str, Any], HTTPStatus]:
        refresh_errors = app.refresh_sessions()
        requested: list[str] = []
        seen: set[str] = set()
        for raw_session in sessions or app.sessions:
            session = str(raw_session or "").strip()
            if not session or session in seen:
                continue
            seen.add(session)
            requested.append(session)
        invalid = [session for session in requested if session not in app.sessions]
        valid = [session for session in requested if session in app.sessions]
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
            return app.session_files_payload_for_infos(
                name,
                {name: info},
                hours,
                from_ref=from_ref,
                to_ref=to_ref,
                repo_refs=repo_refs,
                force=force,
                requester="api-session-files-batch",
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


class ActivityCache:
    """Own retained activity/transcript cache state and warmer lifecycle."""
    def __init__(self, app: "TmuxWebtermApp") -> None:
        self._app = app
        self.state = app.__dict__.pop("__owned_state__activity_cache_state", None) or app.__dict__.get("activity_transcript_service") or ActivityTranscriptService()
        self.watched_pr_truncated_signature: tuple[int, tuple[str, ...]] | None = app.__dict__.pop("__owned_state__activity_cache_watched_pr_truncated_signature", None)
    def demote(self) -> None:
        with self.state.tabber_cache_lock:
            record = self.state.tabber_warmer_record
            self.state.tabber_warmer_record = TabberActivityWarmerRecord()
            self.state.tabber_cache_record.refresh_worker = None
        record.wake.set()
    def watched_prs_payload(self, app, allow_network: bool = True) -> dict[str, Any]: # resolve the github.watched_prs watchlist to live PR metadata, independent of any open # session's branch. The server-side SSE loop refreshes it on a fixed slow cadence so a big watchlist # does not exhaust the GitHub rate limit.
        settings = settings_payload().get("settings", {})
        refs = settings.get("github", {}).get("watched_prs", [])
        result = watched_pr_metadata(refs, app.metadata_cache, allow_network=allow_network)
        # log the truncation only when the capped state CHANGES (count or watchlist), not on
        # every poll — otherwise the event log fills with one identical entry per refresh.
        truncated = result["truncated"]
        signature = (truncated, tuple(str(ref) for ref in refs)) if truncated else None
        if signature != self.watched_pr_truncated_signature:
            self.watched_pr_truncated_signature = signature
            if truncated:
                app.log_event(
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
    def tabber_activity_agents_snapshot(self, app, force: bool = False) -> list[dict[str, Any]]:
        if force:
            payload = app.refresh_tabber_activity_cache()
            agents = payload.get("agents") if isinstance(payload, dict) else []
            return copy.deepcopy(agents) if isinstance(agents, list) else []
        source_signature = app.tabber_activity_source_signature()
        cached = app.get_tabber_activity_cache(app.tabber_activity_refresh_seconds(), allow_stale=True, source_signature=source_signature)
        if cached:
            payload, _fresh, _age_seconds = cached
            agents = payload.get("agents") if isinstance(payload, dict) else []
            return copy.deepcopy(agents) if isinstance(agents, list) else []
        payload = app.refresh_tabber_activity_cache()
        agents = payload.get("agents") if isinstance(payload, dict) else []
        return copy.deepcopy(agents) if isinstance(agents, list) else []
    def activity_session_info_payload( self, app, session: str, info: SessionInfo, work: dict[str, Any], files_payload: dict[str, Any], summary: dict[str, Any], recent_events: list[dict[str, Any]] | None = None, locale: str = "en", ) -> dict[str, Any]:
        selected = info.selected_pane
        agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        git_data = work.get("git") if isinstance(work.get("git"), dict) else {}
        pull_request = work.get("pull_request") if isinstance(work.get("pull_request"), dict) else None
        rolling = app.yoagent_session_summary_record(session)
        latest_summary = str(rolling.get("rolling_summary") or summary.get("local") or "").strip()
        return {
            "session": session,
            "path": str((git_data or {}).get("root") or (git_data or {}).get("cwd") or (agent.cwd if agent else "") or (selected.current_path if selected else "")),
            "cwd": str((agent.cwd if agent else "") or (selected.current_path if selected else "")),
            "tmux_target": str(selected.target if selected else ""),
            "agent": app.compact_agent_for_run_history(agent),
            "git": git_data,
            "pull_request": pull_request,
            "ci": pull_request.get("checks") if isinstance(pull_request, dict) and isinstance(pull_request.get("checks"), dict) else None,
            "linear": work.get("linear") if isinstance(work.get("linear"), list) else [],
            "files": summary.get("files") if isinstance(summary.get("files"), dict) else {},
            "recent_paths": build_recent_agents_payload({session: info}, [session], session_files_by_session={session: files_payload}, locale=locale),
            "latest_summary": truncate_text(latest_summary, 1200),
            "latest_summary_updated_ts": max(0.0, app.float_value(rolling.get("updated_ts"), 0.0)),
            "recent_events": recent_events if recent_events is not None else app.event_log.tail(session=session, limit=5),
            "work": work,
        }
    def cached_activity_work_by_session(self, app) -> dict[str, dict[str, Any]]:
        """Project only already-cached transcript work into one bounded statusd request."""

        result: dict[str, dict[str, Any]] = {}
        encoded_size = 2
        # Project while the immutable cache snapshot is locked. Calling
        # cached_transcripts_work_graph() here would deep-copy the entire canonical graph in the
        # web request before selecting this small projection, retaining much of the CPU being moved.
        with self.state.transcripts_payload_cache_lock:
            payload = self.state.transcripts_payload_cache_record.payload
            cached_sessions = payload.get("sessions") if isinstance(payload, dict) else None
            for session in dict.fromkeys(app.sessions):
                cached_session = cached_sessions.get(session) if isinstance(cached_sessions, dict) else None
                graph = cached_session.get("work_graph") if isinstance(cached_session, dict) else None
                if not isinstance(graph, dict):
                    continue
                work = activity_work_summary_from_graph(graph)
                encoded_entry = json.dumps({session: work}, ensure_ascii=False, separators=(",", ":"))[1:-1].encode("utf-8")
                next_size = encoded_size + (1 if result else 0) + len(encoded_entry)
                if next_size <= STATUSD_ACTIVITY_MAX_WORK_BYTES:
                    result[session] = work
                    encoded_size = next_size
        return result
    def restore_activity_summary_web_state(self, app, payload: dict[str, Any]) -> dict[str, Any]:
        """Restore web-owned rolling-summary attachments and worker state after RPC decode."""

        summaries = payload.get("sessions")
        if isinstance(summaries, dict):
            for session, summary in summaries.items():
                if isinstance(session, str) and isinstance(summary, dict):
                    app.yoagent_controller.attach_yoagent_session_summary(session, summary)
        rolling_updated = app.yoagent_controller.latest_yoagent_session_summary_updated_ts()
        with app.yoagent_summary_worker_lock:
            summary_worker = app.yoagent_summary_worker_record
            worker_state = {
                "first_launch_started": summary_worker.first_launch_started,
                "running": summary_worker.running,
            }
        daemon_state = payload.get("yoagent_summaries")
        payload["yoagent_summaries"] = {
            **(daemon_state if isinstance(daemon_state, dict) else {}),
            **worker_state,
            "updated_ts": rolling_updated,
            "updated_at": datetime.fromtimestamp(rolling_updated, timezone.utc).isoformat() if rolling_updated else "",
        }
        return payload
    def activity_summary_payload(self, app, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        """Decode one daemon-owned activity summary without assembling it in the web process."""

        require_activity_summary_enabled()
        normalized_locale = normalize_locale(locale)
        normalized_scope = app.normalized_activity_session_scope(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(app.float_value(hours, 24.0))
        response, body = app.status_client.activity_summary(
            list(dict.fromkeys(app.sessions)),
            force=bool(force),
            locale=normalized_locale,
            session_scope=normalized_scope,
            hours=bounded_hours,
            work_by_session=app.cached_activity_work_by_session(),
        )
        if response.get("ok") is not True or not body:
            raise ActivitySummaryStatusdUnavailable(response)
        payload = validate_activity_summary(response, body)
        return app.restore_activity_summary_web_state(payload)
    def activity_summary_bytes(self, app, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0) -> tuple[bytes, HTTPStatus]:
        """Return typed HTTP bytes for a completed activity summary or terminal daemon failure."""

        if not activity_summary_enabled():
            metadata, body = activity_summary_disabled_response()
            return body, HTTPStatus(int(metadata["status"]))
        try:
            payload = app.activity_summary_payload(force, locale, session_scope, hours)
        except ActivitySummaryStatusdUnavailable as error:
            failure = {
                "status": "unavailable",
                "error": str(error),
                "terminal": True,
                "upstream": error.response,
            }
            return json.dumps(failure, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), HTTPStatus.FAILED_DEPENDENCY
        except StatusProtocolError as error:
            failure = {
                "status": "upgrade_required",
                "error": str(error),
                "terminal": True,
            }
            return json.dumps(failure, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), HTTPStatus.UPGRADE_REQUIRED
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), HTTPStatus.OK
    def assemble_activity_summary_payload( self, app, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0, work_by_session: dict[str, dict[str, Any]] | None = None, timings: dict[str, float] | None = None, ) -> dict[str, Any]:
        """Assemble the activity summary inside statusd's dedicated app instance."""

        require_activity_summary_enabled()
        started = time.perf_counter()
        locale = normalize_locale(locale)
        session_names, scope_errors, scope = app.activity_session_names(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(app.float_value(hours, 24.0))
        add_phase_timing(timings, "scope_ms", started)
        started = time.perf_counter()
        sessions, errors = discover_sessions(session_names)
        add_phase_timing(timings, "discover_ms", started)
        errors = [*scope_errors, *errors]
        provided_work = work_by_session if isinstance(work_by_session, dict) else {}
        work_signature = app.client_event_payload_signature(provided_work)
        inflight_key = (bool(force), locale, scope, tuple(session_names), bounded_hours, work_signature)
        service = self.state
        with service.activity_summary_lock:
            future = service.activity_summary_futures.get(inflight_key)
            if future is None:
                future = Future()
                service.activity_summary_futures[inflight_key] = future
                owner = True
            else:
                owner = False
        if not owner:
            return copy.deepcopy(future.result())
        try:
            with service.activity_summary_compute_lock:
                payload = app._activity_summary_payload_owner(
                    force=force,
                    locale=locale,
                    session_names=session_names,
                    scope=scope,
                    bounded_hours=bounded_hours,
                    sessions=sessions,
                    errors=errors,
                    work_by_session=provided_work,
                    timings=timings,
                )
            future.set_result(copy.deepcopy(payload))
            return payload
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with service.activity_summary_lock:
                if service.activity_summary_futures.get(inflight_key) is future:
                    service.activity_summary_futures.pop(inflight_key, None)
    def _activity_summary_payload_owner( self, app, *, force: bool, locale: str, session_names: list[str], scope: str, bounded_hours: float, sessions: dict[str, SessionInfo], errors: list[str], work_by_session: dict[str, dict[str, Any]], timings: dict[str, float] | None, ) -> dict[str, Any]:
        service = self.state
        started = time.perf_counter()
        ordered_sessions = app.tmux_recency_ordered_sessions(session_names)
        app.yoagent_controller.prune_yoagent_session_summaries(set(sessions))
        add_phase_timing(timings, "order_ms", started)
        summaries: dict[str, Any] = {}
        ordered_summaries: list[dict[str, Any]] = []
        session_files_by_session: dict[str, SessionFilesPayload] = {}
        transcript_views_by_path: dict[str, dict[str, Any]] = {}
        session_info: dict[str, Any] = {}
        started = time.perf_counter()
        recent_events_by_session = app.event_log.tail_many([session for session in ordered_sessions if session in sessions], limit=5)
        add_phase_timing(timings, "events_ms", started)
        if force:
            with service.activity_summary_lock:
                service.activity_summary_cache.clear()
            app.clear_session_files_cache()
        for session in ordered_sessions:
            info = sessions.get(session)
            if info is None:
                continue
            started = time.perf_counter()
            provided_work = work_by_session.get(session)
            if isinstance(provided_work, dict):
                work = copy.deepcopy(provided_work)
            else:
                work_graph = app.cached_transcripts_work_graph(session)
                if work_graph is None:
                    work_graph = session_work_graph(info, app.metadata_cache, allow_network=False)
                work = activity_work_summary_from_graph(work_graph)
            add_phase_timing(timings, "work_ms", started)
            started = time.perf_counter()
            files_payload = app.cached_session_files_payload_for_info(
                info,
                hours=bounded_hours,
                wait_for_fresh=False,
            )
            add_phase_timing(timings, "files_ms", started)
            session_files_by_session[session] = files_payload
            primary_agent = next((item for item in info.agents if item.transcript), None)
            transcript_view: dict[str, Any] | None = None
            started = time.perf_counter()
            if primary_agent is not None and primary_agent.transcript:
                view_payload, view_status = app.transcript_compact_view(session, 80, info=info, agent_override=primary_agent)
                if view_status == HTTPStatus.OK:
                    transcript_view = view_payload
                    transcript_views_by_path[str(primary_agent.transcript)] = view_payload
            add_phase_timing(timings, "transcripts_ms", started)
            started = time.perf_counter()
            signature = activity_signature(info, work, files_payload)
            cache_key = (locale, session)
            with service.activity_summary_lock:
                cached = service.activity_summary_cache.get(cache_key)
            if cached and cached.get("signature") == signature:
                summary = dict(cached["summary"])
            else:
                if transcript_view is None:
                    summary = build_session_activity_summary(info, work, files_payload, locale=locale)
                else:
                    summary = build_session_activity_summary(info, work, files_payload, locale=locale, transcript_view=transcript_view)
                with service.activity_summary_lock:
                    service.activity_summary_cache[cache_key] = {"signature": signature, "summary": dict(summary)}
            app.yoagent_controller.attach_yoagent_session_summary(session, summary)
            add_phase_timing(timings, "summaries_ms", started)
            summaries[session] = summary
            ordered_summaries.append(summary)
            started = time.perf_counter()
            session_info[session] = app.activity_session_info_payload(
                session,
                info,
                work,
                files_payload,
                summary,
                recent_events=recent_events_by_session.get(session, []),
                locale=locale,
            )
            add_phase_timing(timings, "session_info_ms", started)
        with service.activity_summary_lock:
            for cache_key in list(service.activity_summary_cache):
                if cache_key[1] not in sessions:
                    service.activity_summary_cache.pop(cache_key, None)
        generated = datetime.now(timezone.utc)
        rolling_updated = app.yoagent_controller.latest_yoagent_session_summary_updated_ts()
        with app.yoagent_summary_worker_lock:
            summary_worker = app.yoagent_summary_worker_record
            summary_worker_status = {
                "first_launch_started": summary_worker.first_launch_started,
                "running": summary_worker.running,
            }
        started = time.perf_counter()
        agents = app.tabber_activity_agents_snapshot(force=force) if not transcript_views_by_path else build_recent_agents_payload(sessions, ordered_sessions, session_files_by_session=session_files_by_session, locale=locale, transcript_views_by_path=transcript_views_by_path)
        add_phase_timing(timings, "agents_ms", started)
        started = time.perf_counter()
        global_summary = build_global_activity_summary(ordered_summaries, errors, locale=locale)
        add_phase_timing(timings, "global_ms", started)
        return {
            "generated_at": generated.isoformat(),
            "generated_ts": generated.timestamp(),
            "session_order": [session for session in ordered_sessions if session in summaries],
            "sessions": summaries,
            "session_info": session_info,
            "agents": agents,
            "global": global_summary,
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
    def tabber_activity_session_source_signature( self, app, info: SessionInfo, files_payload: SessionFilesPayload | dict[str, Any], activity_snapshot: dict[str, Any], preclassified_by_target: dict[str, dict[str, Any]], attention_ack_rev: int, owned_rows_for_session: dict[tuple[str, str, str], dict[str, Any]] | None = None, ) -> str:
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
            state = app.agent_window_state_from_screen(screen)
            screen_rows.append((target, state, app.agent_window_attention_signature(state, screen)))
        # `statusd`'s owned roster row (state/attention/cooldown for statusd-classified agents,
        # see agent_window_gathered_agents) is normally a byproduct of the same tmux screen text
        # already folded into `screen_rows` above -- but a roster-only change (e.g. a cooldown
        # timer advancing with no new screen capture) would otherwise be invisible here, so this
        # session would keep reusing a stale owned row until something else changed. Folding the
        # owned rows in directly closes that gap without keying on the GLOBAL roster revision
        # (which would defeat per-session reuse on every unrelated session's roster tick).
        owned_rows_signature = sorted(
            (window, target, kind, app.stable_client_event_payload_signature(row))
            for (_row_session, window, target, kind), row in (owned_rows_for_session or {}).items()
        )
        signature_payload = {
            "info": session_info_cache_signature(info),
            "panes": pane_rows,
            "files": app.session_files_payload_signature(files_payload),
            "activity": activity_rows,
            "screens": screen_rows,
            "attention_ack_rev": attention_ack_rev,
            "owned": owned_rows_signature,
        }
        return app.stable_client_event_payload_signature(signature_payload)
    def tabber_activity_view_coalesce_identity(self, app, scope: str, bounded_hours: float, source_signature: str) -> tuple[str, int]:
        """Cross-port product identity for `tabber_activity_view`, derived from the existing
        per-refresh `source_signature` rather than a new persisted schema field, so this reuses the
        TabberActivityCacheRecord's current reuse/staleness contract unchanged."""
        coalesce_key = f"tabber_activity:{scope}:{bounded_hours}:{source_signature}"[:256]
        generation = int(hashlib.sha256(source_signature.encode("utf-8")).hexdigest()[:12], 16)
        return coalesce_key, generation
    def compute_tabber_activity_rows_via_jobd( self, app, changed_sessions: dict[str, SessionInfo], *, discovered_sessions: dict[str, SessionInfo], session_files_by_session: dict[str, Any], activity_snapshot: dict[str, Any], preclassified_by_session: dict[str, dict[str, dict[str, Any]]], owned_agent_rows: dict[tuple[str, str, str], dict[str, Any]], snapshot_revision: int, scope: str, bounded_hours: float, source_signature: str, locale: str = "en", ) -> dict[str, dict[str, Any]]:
        """Gather impure per-session inputs (tmux screen state, attention/cooldown, path/git) in the
        web owner, then submit the WHOLE changed-session batch to jobd for pure assembly in one call.

        All gathering happens here (a jobd spawn worker has no tmux/app-state access); the worker only
        reconstructs SessionInfo and runs assemble_agent_window_rows/build_recent_agents_payload.
        Raises TabberActivityJobdUnavailable (never falls back to inline assembly here) when jobd
        cannot produce a matching product within the bounded wait; the caller decides the fallback.
        """
        if not changed_sessions:
            return {}
        sessions_payload: dict[str, Any] = {}
        for session, info in changed_sessions.items():
            files_payload = session_files_by_session.get(session, {})
            transcript_views_by_path: dict[str, dict[str, Any]] = {}
            for agent in info.agents:
                if not agent.transcript:
                    continue
                view_payload, view_status = app.transcript_compact_view(session, 80, info=info, agent_override=agent)
                if view_status == HTTPStatus.OK:
                    transcript_views_by_path[str(agent.transcript)] = view_payload
            gathered_agents = app.agent_window_gathered_agents(
                session,
                info=info,
                discovered_sessions=discovered_sessions,
                activity_snapshot=activity_snapshot,
                preclassified_by_target=preclassified_by_session.get(session),
                files_payload=files_payload,
                owned_rows_by_target=owned_agent_rows,
            )
            recent_paths_by_agent = []
            for agent in info.agents:
                window, _pane = session_files.agent_window_for_info(info, agent)
                recent_paths_by_agent.append(
                    recent_agent_paths_from_files(files_payload, agent=agent, window=window)
                )
            sessions_payload[session] = {
                "info": asdict(info),
                "gathered_agents": gathered_agents,
                "recent_paths_by_agent": recent_paths_by_agent,
                "transcript_views_by_path": transcript_views_by_path,
            }
        coalesce_key, generation = app.tabber_activity_view_coalesce_identity(scope, bounded_hours, source_signature)
        response = app.job_client.submit(
            "tabber_activity_view",
            {"sessions": sessions_payload, "locale": locale, "snapshot_revision": snapshot_revision},
            priority="freshness",
            generation=generation,
            coalesce_key=coalesce_key,
            deadline_ms=TABBER_ACTIVITY_JOBD_JOB_DEADLINE_MS,
        )
        if not response.get("ok"):
            raise TabberActivityJobdUnavailable(str(response.get("error") or "jobd submit rejected"))
        try:
            _meta, body, state = wait_for_jobd_product(
                app.job_client, coalesce_key, generation, TABBER_ACTIVITY_JOBD_WAIT_SECONDS
            )
        except JobdProductRpcUnavailable as error:
            raise TabberActivityJobdUnavailable(str(error)) from error
        if body is None:
            raise TabberActivityJobdUnavailable(f"jobd product not ready (state={state or 'none'})")
        data = json.loads(body.decode("utf-8"))
        rows = data.get("session_rows") if isinstance(data, dict) else None
        if not isinstance(rows, dict):
            raise TabberActivityJobdUnavailable("malformed jobd tabber-activity product")
        return rows
    def build_activity_payload(self, app, session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        session_names, scope_errors, scope = app.activity_session_names(session_scope)
        bounded_hours = session_files.bounded_session_files_hours(app.float_value(hours, 24.0))
        sessions, errors = discover_sessions(session_names)
        errors = [*scope_errors, *errors]
        ordered_sessions = app.tmux_recency_ordered_sessions(session_names)
        agent_infos = {session: sessions[session] for session in ordered_sessions if session in sessions and sessions[session].agents}
        session_files_by_session = app.cached_session_files_payloads_for_infos(agent_infos, hours=bounded_hours)
        activity_snapshot = app.activity_snapshot_with_recency()
        # Auto-approve owns prompt/screen classification.  Reuse its immutable roster rows
        # here so activity cannot publish a contradictory state for the same observation.
        # Do not make activity's cold path synchronously build a second roster.  At startup the
        # roster refresh owns the first classification; until it commits, activity keeps its
        # existing path and joins the owned revision on the next refresh.
        roster_payload = app.status_snapshot_payload()
        snapshot_revision, owned_agent_rows = (
            app.agent_window_snapshot_rows_by_target(roster_payload)
            if roster_payload is not None
            else (0, {})
        )
        app.merge_shared_attention_acks()
        with app.client_watch_service.lock:
            attention_ack_rev = app.client_watch_service.attention_ack_rev
        preclassified_by_session: dict[str, dict[str, dict[str, Any]]] = {}
        session_signatures: dict[str, str] = {}
        for session, info in agent_infos.items():
            screens = {
                str(agent.pane_target or ""): app.agent_window_screen_state(agent)
                for agent in info.agents
                if agent.pane_target
            }
            preclassified_by_session[session] = screens
            owned_rows_for_session = {
                key: row for key, row in owned_agent_rows.items() if key[0] == session
            }
            session_signatures[session] = app.tabber_activity_session_source_signature(
                info,
                session_files_by_session.get(session, {}),
                activity_snapshot,
                screens,
                attention_ack_rev,
                owned_rows_for_session,
            )
        with self.state.tabber_cache_lock:
            record = self.state.tabber_cache_record
            can_reuse = record.session_scope == scope and record.session_file_hours == bounded_hours
            previous_signatures = dict(record.session_signatures) if can_reuse else {}
            previous_rows = copy.deepcopy(record.session_rows) if can_reuse else {}
        session_rows: dict[str, dict[str, Any]] = {}
        reused = 0
        changed_sessions: dict[str, SessionInfo] = {}
        for session, info in agent_infos.items():
            signature = session_signatures[session]
            previous = previous_rows.get(session)
            if previous_signatures.get(session) == signature and isinstance(previous, dict):
                session_rows[session] = previous
                reused += 1
                continue
            changed_sessions[session] = info
        rebuilt = 0
        if changed_sessions:
            try:
                jobd_rows = app.compute_tabber_activity_rows_via_jobd(
                    changed_sessions,
                    discovered_sessions=sessions,
                    session_files_by_session=session_files_by_session,
                    activity_snapshot=activity_snapshot,
                    preclassified_by_session=preclassified_by_session,
                    owned_agent_rows=owned_agent_rows,
                    snapshot_revision=snapshot_revision,
                    scope=scope,
                    bounded_hours=bounded_hours,
                    source_signature=app.stable_client_event_payload_signature(sorted(session_signatures.items())),
                )
                for session in changed_sessions:
                    row = jobd_rows.get(session)
                    if isinstance(row, dict):
                        session_rows[session] = row
                        rebuilt += 1
            except TabberActivityJobdUnavailable as exc:
                logger.info("tabber activity batch refresh deferred (jobd) for %d session(s): %s", len(changed_sessions), exc)
            # A changed session jobd could not (re)compute keeps serving its last-known-good rows
            # (stale) rather than substituting an empty payload; a genuinely new session with no
            # prior rows gets an explicit empty shape instead of vanishing from the response.
            for session in changed_sessions:
                if session in session_rows:
                    continue
                previous = previous_rows.get(session)
                session_rows[session] = previous if isinstance(previous, dict) else {"agents": [], "agent_windows": []}
        # `discover_sessions` is intentionally lightweight and can temporarily miss an agent
        # process during tmux/client handoff.  statusd has already committed the authoritative
        # roster for this revision, so do not publish an apparently coherent Tabber payload that
        # erases every known window.  These rows carry status only; normal discovery still owns
        # transcript/path enrichment as soon as it is available again.
        for session in ordered_sessions:
            roster_rows = [
                copy.deepcopy(row)
                for (row_session, _window, _target, _kind), row in owned_agent_rows.items()
                if row_session == session
            ]
            existing = session_rows.get(session)
            existing_windows = existing.get("agent_windows") if isinstance(existing, dict) else None
            if roster_rows and not existing_windows:
                roster_rows.sort(key=lambda row: (
                    app.agent_window_index_key(row.get("window_index") if row.get("window_index") is not None else row.get("window")),
                    str(row.get("pane_target") or ""),
                    str(row.get("kind") or ""),
                ))
                session_rows[session] = {
                    "agents": app.status_roster_recent_agent_rows(session, roster_rows),
                    "agent_windows": roster_rows,
                }
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
        with self.state.tabber_cache_lock:
            record = self.state.tabber_cache_record
            record.session_scope = scope
            record.session_file_hours = bounded_hours
            record.session_signatures = dict(session_signatures)
            record.session_rows = copy.deepcopy(session_rows)
        app.record_performance_sample(
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
    def tabber_activity_source_signature(self, app, session_scope: Any = "configured") -> str: # Acknowledgements change agent-window visibility without changing the process or # transcript identity below. Fold the durable revision into this cache key so every # server stops serving an earlier unacknowledged Tabber snapshot immediately.
        app.merge_shared_attention_acks()
        with app.client_watch_service.lock:
            attention_ack_rev = app.client_watch_service.attention_ack_rev
        session_names, _scope_errors, scope = app.activity_session_names(session_scope)
        sessions, _errors = discover_sessions(session_names)
        tmux_signature = app.stable_client_event_payload_signature(
            app.tmux_signal_signature_payload(app.tmux_signal_snapshot())
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
        key_text = app.client_event_payload_signature(
            {
                "scope": scope,
                "sessions": rows,
                "attention_ack_rev": attention_ack_rev,
                "tmux_signature": tmux_signature,
            }
        )
        return hashlib.sha256(key_text.encode("utf-8")).hexdigest()
    def tabber_activity_cache_disk_path(self, app, hours: float, source_signature: str = "") -> tuple[Path, str]: # A source signature fences freshness inside the record; it must not become # part of the filename. Statusd revisions can legitimately advance while a # Tabber refresh is in flight, and the old design left one durable file per # short-lived signature, then made followers see an empty cache miss.
        del source_signature
        key_text = json.dumps(
            {
                "kind": "tabber-activity",
                "hours": session_files.bounded_session_files_hours(hours),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        signature = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        return TABBER_ACTIVITY_CACHE_DIR / f"{signature}.json", signature
    def tabber_activity_cache_manifest_path(self, app, signature: str) -> Path:
        return TABBER_ACTIVITY_CACHE_DIR / f"{signature}.manifest.json"
    def read_tabber_activity_disk_cache( self, app, hours: float, max_age_seconds: float | None = None, allow_stale: bool = True, source_signature: str = "", allow_source_mismatch: bool = False, ) -> tuple[dict[str, Any], bool, float] | None:
        path, signature = app.tabber_activity_cache_disk_path(hours, source_signature)
        result = app.tabber_published_cache().read(
            path,
            signature,
            published_caches.TabberFreshnessKey(source_signature, hours, allow_source_mismatch),
            max_age_seconds=max_age_seconds,
            allow_stale=allow_stale,
        )
        if result is None:
            return None
        payload = result.payload
        state = result.freshness
        app.set_tabber_activity_cache(payload, stored_at=time.monotonic() - state.age_seconds, write_disk=False, source_signature=source_signature)
        return copy.deepcopy(payload), state.fresh, state.age_seconds
    def tabber_published_cache(self, app):
        return published_caches.tabber_cache(
            version=TABBER_ACTIVITY_CACHE_VERSION,
            payload_signature=app.session_files_payload_signature,
            owner_generation=lambda: app.background_owner.status_payload().get("generation", {}),
            bounded_hours=lambda value: session_files.bounded_session_files_hours(app.float_value(value, 24.0)),
            clock=time.time,
            writer=atomic_write_text,
        )
    def write_tabber_activity_disk_cache_unlocked(self, app, path: Path, signature: str, payload: dict[str, Any], source_signature: str) -> None:
        hours = session_files.bounded_session_files_hours(app.float_value(payload.get("session_file_hours"), 24.0))
        app.tabber_published_cache().write(
            path,
            signature,
            payload,
            published_caches.TabberFreshnessKey(source_signature, hours, False),
        )
    def write_tabber_activity_disk_cache(self, app, payload: dict[str, Any], source_signature: str = "") -> None:
        if not source_signature:
            source_signature = app.tabber_activity_source_signature()
        hours = session_files.bounded_session_files_hours(app.float_value(payload.get("session_file_hours"), 24.0))
        path, signature = app.tabber_activity_cache_disk_path(hours, source_signature)
        try:
            with file_lock(path, dir_mode=0o700):
                app.write_tabber_activity_disk_cache_unlocked(path, signature, payload, source_signature)
        except OSError as exc:
            logger.warning("failed to write tabber activity cache %s: %s", path, exc)
    def set_tabber_activity_cache(self, app, payload: dict[str, Any], stored_at: float | None = None, write_disk: bool = True, source_signature: str = "") -> None:
        if write_disk and not source_signature:
            source_signature = app.tabber_activity_source_signature()
        with self.state.tabber_cache_lock:
            self.state.tabber_cache_record.stored_at = time.monotonic() if stored_at is None else stored_at
            self.state.tabber_cache_record.payload = copy.deepcopy(payload)
            self.state.tabber_cache_record.source_signature = source_signature
        if write_disk:
            app.write_tabber_activity_disk_cache(payload, source_signature=source_signature)
    def get_tabber_activity_cache( self, app, max_age_seconds: float, allow_stale: bool = True, hours: float | None = None, source_signature: str = "", allow_source_mismatch: bool = False, ) -> tuple[dict[str, Any], bool, float] | None:
        started = time.perf_counter()
        now = time.monotonic()
        bounded_hours = session_files.bounded_session_files_hours(24.0 if hours is None else hours)
        stale_cached: tuple[dict[str, Any], bool, float] | None = None
        with self.state.tabber_cache_lock:
            record = self.state.tabber_cache_record
            if record.stored_at is not None and record.payload is not None:
                stored_at = record.stored_at
                payload = record.payload
                cached_hours = session_files.bounded_session_files_hours(app.float_value(payload.get("session_file_hours"), 24.0))
                source_matches = not source_signature or record.source_signature == source_signature
                if cached_hours == bounded_hours and (source_matches or allow_source_mismatch):
                    age_seconds = max(0.0, now - stored_at)
                    fresh = source_matches and age_seconds <= max_age_seconds
                    if fresh:
                        app.record_performance_sample(
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
        disk_cached = app.read_tabber_activity_disk_cache(
            bounded_hours,
            max_age_seconds=max_age_seconds,
            allow_stale=allow_stale,
            source_signature=source_signature,
            allow_source_mismatch=allow_source_mismatch,
        )
        if disk_cached and (stale_cached is None or disk_cached[2] <= stale_cached[2]):
            app.record_performance_sample(
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
            app.record_performance_sample(
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
        app.record_performance_sample(
            BACKGROUND_ROLE_TABBER_ACTIVITY,
            "cache-read",
            trigger="miss",
            compute_ms=(time.perf_counter() - started) * 1000,
            cache_key={"kind": "tabber-activity"},
            cache_status="miss",
            cache_hit=False,
        )
        return None
    def refresh_tabber_activity_cache(self, app, hours: Any = 24.0) -> dict[str, Any]:
        bounded_hours = session_files.bounded_session_files_hours(app.float_value(hours, 24.0))
        source_signature = app.tabber_activity_source_signature()
        inflight_key = (bounded_hours, source_signature)
        with self.state.tabber_cache_lock:
            future = self.state.tabber_cache_record.inflight_by_key.get(inflight_key)
            if future is None:
                future = Future()
                self.state.tabber_cache_record.inflight_by_key[inflight_key] = future
                owner = True
            else:
                owner = False
        if not owner:
            payload = future.result()
            app.record_performance_sample(
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
            payload = app.refresh_tabber_activity_cache_owner(bounded_hours, source_signature)
            future.set_result(copy.deepcopy(payload))
            return payload
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.state.tabber_cache_lock:
                if self.state.tabber_cache_record.inflight_by_key.get(inflight_key) is future:
                    self.state.tabber_cache_record.inflight_by_key.pop(inflight_key, None)
    def refresh_tabber_activity_cache_owner(self, app, bounded_hours: float, source_signature: str) -> dict[str, Any]:
        started = time.perf_counter()
        if not app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            app.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "refresh"})
            cached = app.get_tabber_activity_cache(float("inf"), allow_stale=True, hours=bounded_hours, source_signature=source_signature)
            if cached:
                payload, _fresh, _age = cached
                app.record_performance_sample(
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
            app.record_performance_sample(
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
        with self.state.tabber_cache_lock:
            record = self.state.tabber_cache_record
            current_payload = copy.deepcopy(record.payload) if record.payload is not None else None
            current_signature = record.source_signature
        if current_payload is not None and current_signature == source_signature:
            app.record_performance_sample(
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
        payload = app.build_activity_payload(hours=bounded_hours)
        app.set_tabber_activity_cache(payload, source_signature=source_signature)
        app.record_performance_sample(
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
    def publish_tabber_activity_refresh_if_changed(self, app, *, compute_ms: float) -> bool:
        """Notify demanded Tabber clients after a newly readable cache generation.

        The source signature is an internal cache identity, while the client-event
        broker owns the monotonic delivery revision.  Comparing it under the cache
        lock avoids turning the warmer's unchanged reconciliation into an SSE wakeup.
        """
        with self.state.tabber_cache_lock:
            record = self.state.tabber_cache_record
            source_signature = record.source_signature
            if record.payload is None or not source_signature or source_signature == record.published_source_signature:
                return False
            record.published_source_signature = source_signature
        app.publish_background_refresh_done(
            BACKGROUND_ROLE_TABBER_ACTIVITY,
            {"compute_ms": compute_ms, "cache_changed": True},
        )
        return True
    def run_tabber_activity_cache_refresh(self, app, worker: threading.Thread) -> None:
        try:
            started = time.perf_counter()
            refresh_details = app.background_refresh_event_details(BACKGROUND_ROLE_TABBER_ACTIVITY, {"cache_key_kind": "tabber-activity"}, cache_key={"kind": "tabber-activity"})
            app.log_sampled_background_refresh_event(
                "background_refresh_started",
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "Tabber activity background refresh started",
                refresh_details,
                message_key="events.message.backgroundRefresh.started",
                message_params={"target": message_descriptor("tabber.title", "Tabber")},
            )
            app.refresh_tabber_activity_cache()
            compute_ms = (time.perf_counter() - started) * 1000
            done_details = dict(refresh_details)
            done_details["compute_ms"] = round(compute_ms, 3)
            app.log_sampled_background_refresh_event(
                "background_refresh_done",
                BACKGROUND_ROLE_TABBER_ACTIVITY,
                "Tabber activity background refresh finished",
                done_details,
                message_key="events.message.backgroundRefresh.finished",
                message_params={"target": message_descriptor("tabber.title", "Tabber")},
            )
            app.publish_tabber_activity_refresh_if_changed(compute_ms=compute_ms)
        finally:
            with self.state.tabber_cache_lock:
                if self.state.tabber_cache_record.refresh_worker is worker:
                    self.state.tabber_cache_record.refresh_worker = None
    def start_tabber_activity_cache_refresh(self, app) -> bool:
        if not app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            app.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "async-refresh"})
            return False
        with self.state.tabber_cache_lock:
            if self.state.tabber_cache_record.refresh_worker is not None:
                return False
            worker: threading.Thread

            def run_refresh() -> None:
                app.run_tabber_activity_cache_refresh(worker)

            worker = threading.Thread(target=run_refresh, name="tabber-activity-refresh", daemon=True)
            self.state.tabber_cache_record.refresh_worker = worker
        def rollback() -> None:
            with self.state.tabber_cache_lock:
                if self.state.tabber_cache_record.refresh_worker is worker:
                    self.state.tabber_cache_record.refresh_worker = None

        common.start_thread_with_rollback(worker, rollback)
        return True
    def start_tabber_activity_cache_warmer(self, app) -> bool:
        if not app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            app.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "warmer"})
            return False
        with self.state.tabber_cache_lock:
            current = self.state.tabber_warmer_record
            if current.running and current.thread is not None and current.thread.is_alive():
                return False
            record = TabberActivityWarmerRecord(running=True, consumer_until=current.consumer_until, refresh_due_at=current.refresh_due_at, refresh_triggers=set(current.refresh_triggers))
            worker = threading.Thread(target=app.tabber_activity_cache_warmer_loop, args=(record,), name="tabber-activity-cache", daemon=True)
            record.thread = worker
            self.state.tabber_warmer_record = record

            def rollback() -> None:
                # tabber_cache_lock is already held by this caller; clear the just-published thread
                # in place. capture_thread_owners reads tabber_warmer_record.thread under this same
                # lock and stop_tabber_warmer joins it, so publication and start must be atomic.
                if self.state.tabber_warmer_record is record and record.thread is worker:
                    record.thread = None
                    record.running = False

            # Start under the lock so a teardown capturing tabber_warmer_record.thread in the gap
            # cannot observe or join a published-but-unstarted warmer thread.
            common.start_thread_with_rollback(worker, rollback)
        return True
    def tabber_activity_cache_warmer_loop(self, app, record: TabberActivityWarmerRecord) -> None:
        try:
            while True:
                with self.state.tabber_cache_lock:
                    if self.state.tabber_warmer_record is not record or not record.running:
                        return
                if not app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
                    return
                with self.state.tabber_cache_lock:
                    due_at = record.refresh_due_at
                if due_at <= 0.0:
                    if not app.tabber_activity_has_recent_consumer():
                        app.record_performance_sample(
                            BACKGROUND_ROLE_TABBER_ACTIVITY,
                            "warmer",
                            trigger="idle",
                            cache_key={"kind": "tabber-activity"},
                            cache_status="skipped:no-consumer",
                        )
                    record.wake.clear()
                    record.wake.wait()
                    continue
                remaining = max(0.0, due_at - time.monotonic())
                if remaining:
                    record.wake.clear()
                    record.wake.wait(remaining)
                    continue
                started = time.monotonic()
                try:
                    with self.state.tabber_cache_lock:
                        if record.refresh_due_at != due_at:
                            continue
                        record.refresh_due_at = 0.0
                        record.refresh_triggers.clear()
                    if app.tabber_activity_has_recent_consumer():
                        app.refresh_tabber_activity_cache()
                        app.publish_tabber_activity_refresh_if_changed(compute_ms=(time.monotonic() - started) * 1000)
                except (OSError, RuntimeError, ValueError) as exc:
                    app.log_event(
                        None,
                        "client_event_watch_error",
                        f"Tabber activity cache refresh failed: {exc}",
                        {"diagnostic": str(exc)},
                        message_key="events.message.tabberActivity.refreshFailed",
                    )
        finally:
            with self.state.tabber_cache_lock:
                if self.state.tabber_warmer_record is record:
                    record.running = False
    def empty_tabber_activity_payload(self, app, bounded_hours: float, refresh_seconds: float, **cache: Any) -> dict[str, Any]:
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
    def activity_payload(self, app, hours: Any = 24.0, visible: bool = True) -> tuple[dict[str, Any], HTTPStatus]:
        visible_consumer = app.mark_tabber_activity_consumer(visible)
        refresh_seconds = app.tabber_activity_refresh_seconds()
        bounded_hours = session_files.bounded_session_files_hours(app.float_value(hours, 24.0))
        source_signature = app.tabber_activity_source_signature()
        cached = app.get_tabber_activity_cache(refresh_seconds, allow_stale=True, hours=bounded_hours, source_signature=source_signature)
        if cached is None:
            # A new source generation must never blank a visible Tabber. Reuse the
            # last readable generation as explicitly stale while one owner refreshes.
            cached = app.get_tabber_activity_cache(
                refresh_seconds,
                allow_stale=True,
                hours=bounded_hours,
                source_signature=source_signature,
                allow_source_mismatch=True,
            )
        if cached:
            payload, fresh, age_seconds = cached
            cached_hours = session_files.bounded_session_files_hours(app.float_value(payload.get("session_file_hours"), 24.0))
            if cached_hours != bounded_hours:
                payload = app.build_activity_payload(hours=bounded_hours)
                app.set_tabber_activity_cache(payload, source_signature=source_signature)
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
                elif app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
                    payload["cache"]["refreshing"] = app.start_tabber_activity_cache_refresh()
                else:
                    app.record_background_follower_stale_read(BACKGROUND_ROLE_TABBER_ACTIVITY)
                    refresh_result = app.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "activity-payload-stale"})
                    app.record_background_avoided_recompute(BACKGROUND_ROLE_TABBER_ACTIVITY)
                    if app.background_refresh_should_fallback(refresh_result):
                        payload = app.build_activity_payload(hours=bounded_hours)
                        app.set_tabber_activity_cache(payload, source_signature=source_signature)
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
            return app.empty_tabber_activity_payload(bounded_hours, refresh_seconds, idle_no_consumer=True), HTTPStatus.OK
        if not app.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            refresh_result = app.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "activity-payload"})
            app.record_background_avoided_recompute(BACKGROUND_ROLE_TABBER_ACTIVITY)
            if app.background_refresh_should_fallback(refresh_result):
                payload = app.build_activity_payload(hours=bounded_hours)
                app.set_tabber_activity_cache(payload, source_signature=source_signature)
                payload = copy.deepcopy(payload)
                payload["cache"] = {
                    "hit": False,
                    "stale": False,
                    "age_seconds": 0,
                    "refresh_seconds": refresh_seconds,
                    "fallback": True,
                }
                return payload, HTTPStatus.OK
            return app.empty_tabber_activity_payload(bounded_hours, refresh_seconds, refreshing_elsewhere=True), HTTPStatus.OK
        refreshing = app.start_tabber_activity_cache_refresh()
        return app.empty_tabber_activity_payload(bounded_hours, refresh_seconds, refreshing=refreshing), HTTPStatus.OK


class SystemStatusProjector:
    """Own retained backend-health and system-status projection lifecycle."""
    def __init__(self, app: "TmuxWebtermApp") -> None:
        self._app = app
        self.backend_health_store: Any | None = app.__dict__.pop("backend_health_store", None)
        self.backend_health_liveness_provider: Callable[[], Mapping[str, Any]] | None = app.__dict__.pop("backend_health_liveness_provider", None)
        self.snapshot: system_status_snapshot_module.SystemStatusSnapshotOwner | None = app.__dict__.pop("snapshot", None)

    def stop(self) -> None:
        self._app.stop_system_status_snapshot_owner()
    @staticmethod
    def system_status_metric( value: object, *, running: bool, missing_state: str, missing_reason_code: str, missing_reason: str, ) -> dict[str, object]: # One envelope owner (`local_service_projection.measurement`), two callers: these # three process metrics and the M8 health metrics. The dict used to be built here, # which meant the health block would have been a second copy of the same shape.
        if running:
            return local_service_projection.measurement(
                value,
                state=missing_state,
                reason_code=missing_reason_code,
                reason=missing_reason,
            )
        return local_service_projection.measurement(
            value,
            state="not_running",
            reason_code="not_started",
            reason="Service is not running",
        )
    def system_status_service( self, app, row: dict[str, Any], *, health: local_service_projection.RetainedHealth | None = None, ) -> dict[str, Any]:
        service_id = str(row.get("service") or "").strip()
        labels = {
            "indexd": "Quick Open index",
            "statsd": "YO!stats",
            "jobd": "Filesystem jobs",
            "statusd": "Tmux status",
            # watchd had no entry, so the System row named it "watchd" -- the raw id -- while
            # every other service got a capability name. This label is what the System row and the
            # Daemons roster display verbatim, so a missing entry is a user-visible defect, not a
            # cosmetic one.
            "watchd": "File watching",
            "approvald": "Auto-approval",
        }
        pid = int(row.get("pid") or 0)
        # A demand daemon that idle-exits becomes a zombie until the registry reaper wait()s it:
        # `os.kill(pid, 0)` still succeeds and the service record still names it, so both `pid > 0`
        # here and the `pid`-derived `running` inside `observed_health` would read a dead-and-unreaped
        # child as a running-but-unhealthy service and raise a false "errored". Its `/proc` State is
        # the truth -- `Z` means dead, which for a demand-scoped service is absent-by-design, not an
        # outage. Reading the state distinguishes an idle-exited/zombie daemon (classified idle/absent
        # through the demand path below) from a genuinely-running-but-unhealthy one (state R/S/D with a
        # recorded failure, which still alarms). Only `Z` is treated as dead: a `/proc`-less host
        # returns "" for every pid, and a fully-gone pid is already fenced to 0 by the identity read.
        pid_zombie = pid > 0 and local_services_registry.process_state(pid) == "Z"
        health_row = {**row, "pid": 0} if pid_zombie else row
        pid = 0 if pid_zombie else pid
        running = pid > 0
        transport_reason = str(row.get("transport_reason") or "").strip()
        last_failure = str(row.get("last_failure") or "").strip()
        # A service that is spawned on first use is absent by design until something asks
        # for it. Absence alone therefore cannot mean "down" -- only a recorded reason can.
        # This used to be keyed off `healthy is not False`, but every runtime_status coerces
        # healthy to a bool, so the idle branch was unreachable and a legitimately-absent
        # watchd classified exactly like a broken daemon.
        #
        # ONE DERIVATION, TWO VOCABULARIES -- THE RECORDED DECISION
        # ---------------------------------------------------------
        # This panel used to decide `running`/`idle`/`issue`/`unavailable` from the row itself,
        # in parallel with `backend_health.observer.observed_health` deciding
        # `ready`/`starting`/`degraded`/`down` from the same row. Two classifiers, one fact:
        # an absent jobd on a process that lost the scheduler lease read `unavailable` and
        # `alerting` here while the topbar indicator read `starting` and stayed quiet, because
        # only the observer knew about `absence_expected_reason`.
        #
        # The single owner is `observed_health`. It is the typed reducer the health contract
        # names, it is what the retained store and the topbar indicator already consume, and it
        # is the one that reads all five distinct absence causes. The panel consumes it here and
        # is NOT a second copy: this method still owns only the RENDERING vocabulary -- which of
        # its four display states, which bounded `reason_code`, and which human sentence -- so
        # the existing System/API contract is unchanged while the decision behind it is shared.
        # Copying `observed_health`'s branches into this file was the rejected alternative; that
        # is a third copy, not a fix.
        #
        # `demand_started` is still read here, before `essential`, exactly as before: the
        # ordering assertion in tests/test_backend_health_catalog.py pins that absence-by-design
        # is classified before essentiality is consulted, and the sentence a demand-scoped
        # service shows ("Starts on demand") is a different sentence from a pending pin.
        demand_started = row.get("demand_started") is True
        health_state, _health_reason_code = observed_health(health_row)
        if health_state == "ready":
            state, reason_code, reason = "running", "", ""
        elif health_state == "starting":
            # Not serving, and not a failure: absent by design, or a named owner in this process
            # is still bringing it up. Neither is an alert.
            state, reason_code = "idle", "not_started"
            reason = "Starts on demand" if demand_started else "Starting"
        else:
            state = "issue" if running else "unavailable"
            reason_code = "transport_failed" if transport_reason else "service_unavailable"
            reason = transport_reason or last_failure or "Service did not report healthy status"
        essential = service_id in ESSENTIAL_LOCAL_SERVICES
        resources = row.get("resources") if isinstance(row.get("resources"), dict) else {}
        details = {
            key: value
            for key, value in row.items()
            if key not in {"service", "pid", "started_at", "uptime_seconds", "resources"}
        }
        return {
            **row,
            "id": service_id,
            "label": labels.get(service_id, service_id),
            "state": state,
            "reason_code": reason_code,
            "reason": reason,
            "essential": essential,
            # The one predicate the UI may key a visible outage on. Absence by design is
            # already excluded above (it classifies as "idle"), so this stays true to the
            # requirement -- any service that recorded a failure is shown, essential or
            # not -- and a second copy of the rule cannot drift from this one.
            "alerting": state in {"issue", "unavailable"},
            "metrics": {
                "cpu_now_percent": app.system_status_metric(
                    resources.get("cpu_percent"),
                    running=running,
                    missing_state="warming",
                    missing_reason_code="baseline_pending",
                    missing_reason="Waiting for a second cumulative CPU sample",
                ),
                "rss_bytes": app.system_status_metric(
                    resources.get("rss_bytes"),
                    running=running,
                    missing_state="unavailable",
                    missing_reason_code="process_read_failed",
                    missing_reason="The operating system did not return process memory",
                ),
                "uptime_seconds": app.system_status_metric(
                    row.get("uptime_seconds"),
                    running=running,
                    missing_state="unavailable",
                    missing_reason_code="start_time_unavailable",
                    missing_reason="The service start time is unavailable",
                ),
            },
            # M8. The retained observation this row could not carry before: typed state and
            # reason, when that state started, the bounded transition history, how complete
            # each aggregate is, and the restart/request/error/latency numbers. `metrics`
            # above is left at exactly its three process measurements -- the panel and
            # `tests/test_gate_panels.py:164` pin that set, and the retained numbers have a
            # different source and a different denominator, so they are published here with
            # their own coverage rather than smuggled into it.
            "health": (health if health is not None else local_service_projection.RetainedHealth()).service(service_id),
            "details": details,
        }
    def stats_current_recovery_events(self, app, migration_status: dict[str, Any]) -> list[dict[str, str]]:
        issues = migration_status.get("issue_records")
        if not isinstance(issues, (list, tuple)):
            return []
        database_path = app.stats_current_client.database_path
        events: list[dict[str, str]] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            kind = str(issue.get("kind") or "").strip()
            if kind not in {"unreadable_current_database", "unsupported_legacy_database"}:
                continue
            source = Path(str(issue.get("source") or "")).name
            if not source:
                continue
            events.append({
                "subsystem": "statsd",
                "event": kind,
                "quarantined_artifact": source,
                "quarantined_path": str(database_path.parent / source),
                "destination_path": str(database_path),
                "reason": str(issue.get("detail") or "Stats history was recovered from a damaged database")[:256],
            })
        return events[:16]
    def statsd_runtime_status(self, app) -> dict[str, Any]:
        """Build statsd's whole row, the same way the other five services build theirs.

        Until M3 this was an inline dict literal inside `runtime_local_services()`, so
        statsd's row shape lived in two places -- its client's projection and the composed
        projection -- and only statsd's did. It is now one named row producer like every
        other service, which is what lets the collector treat all six identically.
        """
        current_runtime = app.stats_current_runtime.status()
        current_service = current_runtime.get("service") if isinstance(current_runtime.get("service"), dict) else {}
        current_service = app.stats_current_client.runtime_status(current_service)
        migration = current_service.get("migration") if isinstance(current_service.get("migration"), dict) else {}
        build = current_service.get("build") if isinstance(current_service.get("build"), dict) else {}
        service_usage = current_service.get("usage") if isinstance(current_service.get("usage"), dict) else {}
        transcript_usage = app.stats_current_transcript_usage.status()
        token_family = current_runtime.get("families", {}).get("agent_tokens", {}) if isinstance(current_runtime.get("families"), dict) else {}
        token_cadence = float(token_family.get("cadence_seconds") or STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS) if isinstance(token_family, dict) else STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS
        usage = dict(service_usage)
        usage["transcripts"] = transcript_usage
        usage["health"] = stats_current_usage_health(
            service_usage,
            transcript_usage,
            token_cadence,
            sampler_families=current_runtime.get("families") if isinstance(current_runtime.get("families"), dict) else {},
        )
        return local_service_projection.local_service_runtime_row(
            "statsd",
            pid=int(current_service.get("pid") or 0),
            started_at=float(current_service.get("started_at") or 0.0),
            version=int(current_service.get("version") or 0),
            healthy=current_service.get("ok") is True and migration.get("state") != "failed",
            last_failure=str(migration.get("failure") or build.get("last_failure") or ""),
            resources=current_service.get("resources") if isinstance(current_service.get("resources"), dict) else {},
            fields_before_failure={
                "clients": int(current_service.get("clients") or 0),
                "queues": current_service.get("queue") if isinstance(current_service.get("queue"), dict) else {},
                "cache": current_service.get("warm") if isinstance(current_service.get("warm"), dict) else {},
                "migration": migration,
                "build": build,
                "delta": current_service.get("delta") if isinstance(current_service.get("delta"), dict) else {},
                "sampler_families": current_runtime.get("families") if isinstance(current_runtime.get("families"), dict) else {},
                "usage": usage,
            },
            fields_after_failure={
                # The one excuse statsd may state, and only while this process is mid-flight taking
                # the pin. `observed_health` reads it AFTER `last_failure`/`transport_reason`, so a
                # statsd that recorded a real failure alarms whether or not the pin is pending.
                "absence_expected_reason": STATSD_ABSENT_WHILE_PIN_PENDING if statsd_pin_pending(current_runtime) else "",
            },
        )
    def local_services_row_producers(self, app) -> dict[str, Callable[[], dict[str, Any]]]:
        """The one map from service id to the callable that owns its whole row.

        Resolved per collection, not cached: tests and runtime both replace client
        objects on this app, and a producer bound once at construction would keep
        calling the client that existed then.
        """
        return {
            "indexd": app.search_indexer.runtime_status,
            "statsd": app.statsd_runtime_status,
            "jobd": app.job_client.runtime_status,
            "statusd": app.status_client.runtime_status,
            "watchd": app.watchd_runtime_status,
            "approvald": app.approval_client.runtime_status,
        }
    def local_services_recovery_entrypoints(self, app) -> dict[str, Callable[[], bool]]:
        """The one map from service id to that service's OWN client `retry` wrapper.

        The recovery mirror of `local_services_row_producers()` above, and deliberately the same
        shape: one owner, one map, resolved per call so a replaced client is the one retried. No
        caller may retry a service any other way -- a per-service retry scattered across the
        recovery path is how two callers end up with two ladders for one service.

        Five of the six inventory services are here. indexd is absent because
        `SearchIndexerClient` is not a `LocalServiceClient` and declares no retry wrapper at all;
        that gap is the catalog's `recovery_client_entrypoint == ""` row, and
        `tests/test_backend_health_catalog.py` derives this map's key set from it rather than
        letting the two lists drift.

        Every value below is a client wrapper that clears the latched failure through
        `LocalServiceRegistry.retry` and then asks for a start. None of them stops, signals or
        reclaims anything, which is what makes `LocalServiceRecoveryControl` non-destructive by
        construction rather than by review.
        """
        return {
            "statsd": app.stats_current_client.retry,
            "jobd": app.job_client.retry,
            "statusd": app.status_client.retry,
            "watchd": app.watch_client.retry,
            "approvald": app.approval_client.retry,
        }
    def local_services_recovery_control(self, app) -> LocalServiceRecoveryControl:
        """The control the backend-health observer is constructed with (`cli.py`).

        Handed the bound map method, not the map, so the control resolves clients at the moment
        it retries. This is the only production place a recovery control is built.
        """

        return LocalServiceRecoveryControl(app.local_services_recovery_entrypoints)
    def local_services_recovery_events(self, app, rows: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
        """Derive the statsd recovery banner from the collected row, not a second read.

        The migration status used to be captured in the projection's own body because the
        statsd row was built there. Reading it back off the collected row keeps exactly one
        statsd status read per collection.
        """
        statsd_row = rows.get("statsd") or {}
        migration = statsd_row.get("migration")
        return app.stats_current_recovery_events(migration if isinstance(migration, Mapping) else {})
    def local_services_snapshot(self, app) -> local_service_projection.LocalServicesSnapshot:
        """Collect the one immutable local-services snapshot.

        This is the single owner. `runtime_local_services()` renders it for HTTP, and the
        `service_load` stats collector samples it; neither builds rows of its own. No call
        below issues a start: every producer reads status or a persisted record, so a full
        projection starts zero demand-scoped services.
        """
        collector = local_service_projection.LocalServicesCollector(
            app.local_services_row_producers,
            ledger=app.runtime_process_ledger,
            recovery_events=app.local_services_recovery_events,
        )
        return collector.collect()
    def attach_backend_health_store(self, app, store: Any) -> None:
        """Hold this port's live retained-health store so the projection never reads its file.

        RECORDED DECISION (M8) -- how a System row reaches the retained history.

        The store is PUSHED in here, once, by whoever armed this port's observer
        (`cli.start_backend_health_observer`). The HTTP request thread then reads the
        document the observing process already holds in memory. It never opens
        `STATE_DIR/backend-health/<port>.json`.

        The rejected alternative was for the projection to construct its own store and call
        `load()`. That is one directory `file_lock`, one open, one read and one JSON parse on
        every `/api/system-status`, contending with the observer's own 2-second locked write,
        to reproduce a document this process already has. It also cannot see the live
        persistence state at all: a store that is failing to write says so only in memory
        (`BackendHealthStore.persistence_status`), so a file reader would report a healthy
        monitor that has not published in an hour.

        The cost of the push is stated rather than hidden: `BackendHealthStore.status()`
        round-trips the document through `json.dumps`/`json.loads`, so each
        `/api/system-status` pays one bounded deep copy -- six resources, at most 128
        retained transition rows each -- in CPU, with no I/O and no lock. It is taken ONCE
        per projection, not once per row. `record()` rebinds `_document` to a freshly built
        dict and mutates no part of the previous one, so this read needs no lock to be
        consistent.

        When nothing is attached -- any process that never armed an observer, and every unit
        test that does not ask for one -- the projection publishes
        `reason_code: "observer_unattached"` and null metrics. It does not render zeros.
        """
        self.backend_health_store = store
    def attach_backend_health_observer(self, app, observer: Any) -> None:
        """Hold the observer's liveness reader beside its history store."""
        self.backend_health_liveness_provider = observer.liveness
    def retained_backend_health(self, app) -> local_service_projection.RetainedHealth:
        """Collect the two in-memory retained-health inputs ONCE per projection.

        Both reads are process-local: the observer's published document, and this web
        process's own RPC ledger. Neither opens a socket, starts a service, or touches disk.
        """
        store = self.backend_health_store
        document = store.status() if store is not None else {}
        # Liveness comes from the OBSERVER, not the store: only it knows the cadence and owns the
        # thread whose survival is the question, and it answers on the monotonic clock it
        # schedules on rather than a wall clock that can step.
        provider = self.backend_health_liveness_provider
        liveness = provider() if provider is not None else {}
        return local_service_projection.RetainedHealth(
            document=document if isinstance(document, dict) else {},
            liveness=liveness if isinstance(liveness, dict) else {},
            traffic=local_service_traffic_snapshot(),
            now=time.time(),
            web_process_started_at=SERVER_STARTED_AT,
        )
    def runtime_local_services(self, app) -> dict[str, Any]:
        """Return bounded worker diagnostics without exposing service payloads."""
        health = app.retained_backend_health()
        return app.local_services_snapshot().payload(
            lambda row: app.system_status_service(row, health=health),
            health=health,
        )
    def runtime_process_ledger(self, app) -> dict[str, Any]:
        """Bounded identity-verified process-group ledger for System diagnostics.

        Same identity source the launch preflight and overload watchdog use, so
        restart, containment, and diagnostics always agree on which PIDs belong
        to this port. Fields stay bounded: names, PIDs, groups, and the newest
        overload-evidence path — never command lines or payloads.
        """
        table = local_services_registry.bounded_process_table()
        port = int(getattr(app.background_owner, "port", 0) or 0) if hasattr(self, "background_owner") else 0
        port_group = local_services_registry.tracked_port_process_group(port, common.STATE_DIR, table) if port else {}
        service_dir = common.STATE_DIR / "services"
        tracked_groups = local_services_registry.tracked_local_service_groups(service_dir, table)
        service_groups = [
            {
                key: group[key]
                for key in ("service", "pid", "pgid", "member_pids", "launcher_pid", "launcher_port")
            }
            for group in tracked_groups
        ]
        untracked_orphans = local_services_registry.untracked_local_service_processes(
            service_dir,
            table,
            tracked_groups,
        )
        evidence = sorted(Path("/tmp").glob(f"yolomux-overload-{port}-*.json")) if port else []
        return {
            "port_group": port_group,
            "service_groups": service_groups,
            "untracked_orphans": untracked_orphans,
            "last_overload_evidence": str(evidence[-1]) if evidence else "",
        }
    def runtime_filesystem_batch_rows(self, app, metrics: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
        """Return only the safe fs-batch attribution fields from bounded perf records."""
        rows: list[dict[str, Any]] = []
        recent = metrics.get("recent") if isinstance(metrics, dict) else []
        for item in recent if isinstance(recent, list) else []:
            if not isinstance(item, dict) or item.get("role") != "http-endpoint" or item.get("surface") != "POST /api/fs/batch":
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            if details.get("fs_batch") is not True:
                continue
            rows.append({
                "time": float(item.get("time") or 0.0),
                "compute_ms": float(item.get("compute_ms") or 0.0),
                "payload_bytes": int(item.get("payload_bytes") or 0),
                "batch_size": int(details.get("fs_batch_size") or 0),
                "operations": str(details.get("fs_batch_operations") or "{}"),
                "path_hashes": str(details.get("fs_batch_path_hashes") or "[]"),
                "triggers": str(details.get("fs_batch_triggers") or "{}"),
                "client_revision": str(details.get("fs_batch_client_revision") or "unknown"),
                "client_scope": str(details.get("fs_batch_client_scope") or "legacy"),
            })
        return rows[-max(1, int(limit or 8)):]
    def runtime_control_report_payload(self, app) -> dict[str, Any]:
        """Serve the control socket from in-memory endpoint evidence only.

        The full System report may scan cache trees and query local services. That work can block
        the single control-server thread precisely while diagnosing a loaded server, so the CLI
        report uses this small in-memory projection instead.
        """
        status = app.background_owner.status_payload()
        diagnostics = app.performance_diagnostics_payload()
        metrics = diagnostics.get("perf") if isinstance(diagnostics.get("perf"), dict) else {}
        client_events = app.client_events.snapshot()
        chat_events = {
            event_type: {
                "published": int(client_events.get("published_by_type", {}).get(event_type, {}).get("events", 0)),
                "delivered": int(client_events.get("delivered_by_type", {}).get(event_type, {}).get("events", 0)),
            }
            for event_type in ("chat_messages_changed", "chat_typing_changed")
        }
        # Keep the documented report shape without cache walks, service probes, event-log reads,
        # or transcript scans on the single control-server thread.
        bounded_cache = lambda path: {"path": str(path), "exists": Path(path).exists(), "files": 0, "dirs": 0, "bytes": 0, "errors": 0, "truncated": True}
        return {
            "ok": True,
            "state_dir": str(common.STATE_DIR),
            "owner": {
                "current_owner": status.get("current_owner"),
                "status": status.get("status"),
                "owner": bool(status.get("owner")),
                "search_index": status.get("search_index"),
                "debug": {},
                "control": {"ok": True, "source": "live-owner-control"},
            },
            "refresh": {"bounded": True, "roles": status.get("roles", {}), "counters": status.get("counters", {}), "coalescing": status.get("coalescing", {}), "local_refreshing": {}, "dependency_invalidations": {}, "recurring_work": []},
            "caches": {
                "session_files": bounded_cache(SESSION_FILES_CACHE_DIR),
                "activity": bounded_cache(TABBER_ACTIVITY_CACHE_DIR),
                "search_index": bounded_cache(file_index.INDEX_DIR),
            },
            "search_index": {},
            "local_services": {"services": [], "totals": {}, "ledger": {}, "bounded": True},
            "top_endpoints": app.runtime_top_endpoints(diagnostics),
            "top_background_work": app.runtime_top_background_work(diagnostics),
            "top_event_types": [],
            "client_events": client_events,
            "chat": {**app.chat_service.diagnostics(), "subscribers": int(client_events.get("channel_counts", {}).get("chat", 0)), "events": chat_events},
            "login_throttle": {**app.login_rate_limiter.diagnostics(), "edge": app.login_edge_controller.diagnostics()},
            "largest_active_transcripts": [],
            "transcripts_cache": {},
            "filesystem_batch": app.runtime_filesystem_batch_rows(metrics),
        }
    def runtime_report_core( self, app, *, background_status: dict[str, Any] | None = None, owner_control_response: dict[str, Any] | None = None, local_services: dict[str, Any] | None = None, ) -> dict[str, Any]:
        """The half of the report the Daemons roster SCANS: the service roster and its identity.

        Split from `runtime_report_advanced` because these two halves have different demand. This
        half is what a visible panel refreshes on its poll; the other half is what a reader opens
        deliberately. Building them together meant transcript scans and performance folds ran on
        every five-second poll of a panel whose Advanced section was closed.

        The split is by CONSUMER, not by taste: every key here has a reader outside the Advanced
        disclosure, and every key in the other half has either an Advanced-only reader or none.
        """

        status = background_status if isinstance(background_status, dict) else app.background_owner.status_payload()
        client_events = app.client_events.snapshot()
        chat_events = {
            event_type: {
                "published": int(client_events.get("published_by_type", {}).get(event_type, {}).get("events", 0)),
                "delivered": int(client_events.get("delivered_by_type", {}).get(event_type, {}).get("events", 0)),
            }
            for event_type in ("chat_messages_changed", "chat_typing_changed")
        }
        services = local_services if isinstance(local_services, dict) else app.runtime_local_services()
        return {
            "ok": True,
            "state_dir": str(common.STATE_DIR),
            "owner": {
                "current_owner": status.get("current_owner"),
                "status": status.get("status"),
                "owner": bool(status.get("owner")),
                "search_index": status.get("search_index"),
            },
            "caches": {
                "session_files": app.runtime_cache_dir_stats(SESSION_FILES_CACHE_DIR),
                "activity": app.runtime_cache_dir_stats(TABBER_ACTIVITY_CACHE_DIR),
                "search_index": app.runtime_cache_dir_stats(file_index.INDEX_DIR),
            },
            "search_index": (
                owner_control_response.get("search_index_runtime")
                if isinstance(owner_control_response, dict) and isinstance(owner_control_response.get("search_index_runtime"), dict)
                else file_index.runtime_diagnostics()
            ),
            "local_services": services,
            "client_events": client_events,
            "chat": {
                **app.chat_service.diagnostics(),
                "subscribers": int(client_events.get("channel_counts", {}).get("chat", 0)),
                "events": chat_events,
            },
            "tmux_signal_watcher": app.tmux_signal_event_watcher_status(),
        }
    def runtime_report_advanced( self, app, *, background_status: dict[str, Any] | None = None, owner_debug: dict[str, Any] | None = None, owner_control_response: dict[str, Any] | None = None, force_transcripts: bool = True, local_services: dict[str, Any] | None = None, ) -> dict[str, Any]:
        """The half a reader consults deliberately: refresh coordination, top-N folds, transcripts.

        `local_services` is INJECTED rather than defaulted away. The approvald recurring-work row
        below is read out of the local-service roster, and a `None` default would have published a
        row of confident zeros for a subsystem nobody measured this cycle. When the caller has
        already collected the roster it passes it; when this half is produced on its own it pays
        for its own collection.
        """

        status = background_status if isinstance(background_status, dict) else app.background_owner.status_payload()
        # Remote control responses from older servers may still carry perf, while the current
        # topbar status deliberately does not.  Keep the report's diagnostics source explicit.
        diagnostic_status = dict(status)
        if not isinstance(diagnostic_status.get("perf"), dict):
            diagnostic_status.update(app.performance_diagnostics_payload())
        transcript_payload = app.transcripts_payload(force=force_transcripts)
        services = local_services if isinstance(local_services, dict) else app.runtime_local_services()
        return {
            "owner": {
                "debug": app.runtime_owner_debug_summary(owner_debug),
                "control": app.runtime_owner_control_summary(owner_control_response),
            },
            "refresh": app.runtime_refresh_state(status, services),
            "top_endpoints": app.runtime_top_endpoints(diagnostic_status),
            "top_background_work": app.runtime_top_background_work(diagnostic_status),
            "top_event_types": app.runtime_top_event_types(),
            # Privacy-safe login-throttle aggregates: allowed/blocked-by-scope counts,
            # active rows, locked accounts, decision latency — never raw usernames/IPs.
            "login_throttle": {
                **app.login_rate_limiter.diagnostics(),
                "edge": app.login_edge_controller.diagnostics(),
            },
            "largest_active_transcripts": app.runtime_largest_transcripts(transcript_payload),
            "transcripts_cache": transcript_payload.get("cache", {}) if isinstance(transcript_payload, dict) else {},
        }
    def runtime_report_payload( self, app, *, background_status: dict[str, Any] | None = None, owner_debug: dict[str, Any] | None = None, owner_control_response: dict[str, Any] | None = None, force_transcripts: bool = True, ) -> dict[str, Any]:
        """The whole report: both halves, one local-service collection, one merge rule.

        The CLI/control report and the composed system-status payload both want everything, so the
        composition lives here once rather than as a second construction beside each caller.
        """

        status = background_status if isinstance(background_status, dict) else app.background_owner.status_payload()
        local_services = app.runtime_local_services()
        core = app.runtime_report_core(
            background_status=status,
            owner_control_response=owner_control_response,
            local_services=local_services,
        )
        advanced = app.runtime_report_advanced(
            background_status=status,
            owner_debug=owner_debug,
            owner_control_response=owner_control_response,
            force_transcripts=force_transcripts,
            local_services=local_services,
        )
        # `owner` is the one key both halves contribute to, so it is merged explicitly here rather
        # than letting a dict splat silently drop the cheap identity fields.
        return {**core, **advanced, "owner": {**core["owner"], **advanced["owner"]}}
    def system_status_server_block(self, app, sample: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """Publish the web process's own row through the ONE metric-envelope owner.

        `local_service_projection.measurement` already publishes every local service's
        CPU/memory/uptime as a typed envelope. This block used to be the one exception --
        plain floats built with `float(sample.get(...) or 0.0)` -- which is why an unpushed
        sample arrived at the panel as a finite `0` and was stamped `measured`. It is the
        same divergent-copy defect the panel's own comments forbid, so the web process now
        goes through the same envelope as the six services beside it: a value nobody
        sampled is `{"state": "unavailable", "value": None, "reason_code": ...}`, and the
        roster's Memory/CPU totals skip it exactly as they already skip every other
        unmeasured row.

        `version`, `pid` and `started_at` are NOT measurements -- they are read here, are
        always known, and stay plain scalars.
        """

        reason_code = str(sample.get("reason_code") or STATS_SAMPLE_NOT_PUSHED_REASON_CODE)
        reason = str(sample.get("reason") or STATS_SAMPLE_NOT_PUSHED_REASON)
        # A sample that stopped arriving is not a current measurement. `cpu_budget` already aged
        # its own copy of this record and said `stale`, but these envelopes carried no age at all,
        # so a frozen sample kept rendering as `measured` at its last value forever -- the last way
        # this panel could present something unmeasured as measured.
        read_at = float(now if now is not None else time.time())
        age_seconds = stats_current_host_collectors.host_cpu_sample_age_seconds(sample, read_at)
        if sample.get("cpu_percent") is not None:
            if age_seconds is None:
                # A value with no push timestamp. Its currency cannot be checked, and the one
                # thing this must not do is invent an age: `STATS_SAMPLE_STALE_REASON.format(
                # seconds=int(age_seconds or 0))` would have printed a confident "is 0s old"
                # about a sample whose age is exactly what is unknown.
                reason_code = STATS_SAMPLE_UNDATED_REASON_CODE
                reason = STATS_SAMPLE_UNDATED_REASON
                sample = {**sample, "cpu_percent": None, "system_cpu_percent": None, "rss_bytes": None}
            elif stats_current_host_collectors.host_cpu_sample_is_stale(age_seconds):
                reason_code = STATS_SAMPLE_STALE_REASON_CODE
                reason = STATS_SAMPLE_STALE_REASON.format(seconds=int(age_seconds))
                sample = {**sample, "cpu_percent": None, "system_cpu_percent": None, "rss_bytes": None}

        def envelope(value: object) -> dict[str, Any]:
            return local_service_projection.measurement(
                value, state="unavailable", reason_code=reason_code, reason=reason,
            )

        return {
            "version": YOLOMUX_VERSION,
            # These two fall back to a locally KNOWN truth, not to zero, which is why they
            # keep their `or`: this process's pid and start time are never unknown here.
            "pid": int(sample.get("pid") or os.getpid()),
            "started_at": float(sample.get("started_at") or SERVER_STARTED_AT),
            # Uptime is DERIVED HERE, from this process's own start time and the one moment this
            # response describes. The comment above this line used to say exactly that while the
            # code published `envelope(sample["uptime_seconds"])` -- a value written only when a
            # statsd CPU push arrived. So when delivery broke, CPU and RSS correctly went
            # `unavailable` past the stale window while uptime sat FROZEN at the last push and
            # stayed stamped `measured`: a number that stops advancing, presented as current,
            # precisely when the reader is looking at the panel to find out what broke. A live
            # smoke could not see it, because a healthy push made the frozen field advance.
            #
            # There is no cached copy left to disagree with this one: `handle_control_request` no
            # longer writes `uptime_seconds` into the sample, and `latest_stats_sample` no longer
            # synthesizes one. One field, one source, and it cannot freeze because nothing outside
            # this process feeds it.
            "uptime_seconds": envelope(max(0.0, read_at - SERVER_STARTED_AT)),
            "cpu_percent": envelope(sample.get("cpu_percent")),
            "system_cpu_percent": envelope(sample.get("system_cpu_percent")),
            "rss_bytes": envelope(sample.get("rss_bytes")),
        }
    def system_status_core_payload(self, app) -> dict[str, Any]:
        """The body the Daemons roster polls for. Produced in the background, never on a request.

        This runs on the snapshot owner's thread; `/api/system-status` only reads what it
        published. That is the whole point of the split - the panel's five-second poll used to
        carry this entire assembly, so a server that was busy served its own diagnostics slowest.
        """
        # Diagnostics are a reader. Only the CPU family worker may advance the
        # process/host baselines; otherwise a System refresh can consume the
        # next one-second observation and leave no durable bucket for it.
        # Assemble the SLOW runtime data FIRST, then take exactly one reading of the CPU sample
        # and one timestamp, and render the whole response from that single moment.
        #
        # The old order read the sample, then did the slow work, then rendered the now-aged
        # sample while `cpu_budget` re-read the cache and saw a newer push. One live response
        # reported `server` stale at 5s and `cpu_budget.sample_age_seconds` 0.358 at the same
        # time: the response manufactured its own staleness and flipped the row to an em dash
        # for a reason that had nothing to do with statsd.
        runtime_report = app.runtime_report_core()
        generated_at = time.time()
        sample = app.latest_stats_sample()
        return {
            **runtime_report,
            "generated_at": generated_at,
            "server": app.system_status_server_block(sample, now=generated_at),
            "cpu_budget": app.server_cpu_budget_payload(now=generated_at, sample=sample),
            # System mirrors the same canonical matrix used by the current
            # capabilities endpoint; there is no diagnostic-only policy copy.
            "resolution_capabilities": stats_resolution.wire_capabilities(),
            "stats_current": app.stats_current_runtime.status(),
            "host": collect_host_diagnostics().payload(admin=True),
        }
    def system_status_advanced_payload(self, app) -> dict[str, Any]:
        """The Advanced-disclosure body, produced only when somebody has asked for it.

        `force_transcripts=False` for the same reason the composed payload used it: a diagnostics
        read must not drive a transcript refresh, only report the one already cached.
        """

        return {
            "ok": True,
            "generated_at": time.time(),
            **app.runtime_report_advanced(force_transcripts=False),
        }
    def system_status_payload(self, app) -> dict[str, Any]:
        """Both halves of the System view, composed. The CLI report and contract tests read this.

        The route does NOT: it reads the published core snapshot, and the Advanced disclosure reads
        the separately retained advanced body. This composition exists so a caller that genuinely
        wants everything at once has one place to get it rather than a second assembly of its own.
        """

        core = app.system_status_core_payload()
        advanced = app.system_status_advanced_payload()
        return {
            **core,
            **{key: value for key, value in advanced.items() if key not in {"ok", "generated_at"}},
            "owner": {**core["owner"], **advanced["owner"]},
        }
    def attach_system_status_snapshot_owner(self, app, owner: system_status_snapshot_module.SystemStatusSnapshotOwner) -> None:
        """Hold the ONE owner of the retained system-status bodies."""

        self.snapshot = owner
    def start_system_status_snapshot_owner(self, app) -> bool:
        """Build and start the owner once. Returns False when one is already attached."""

        if self.snapshot is not None:
            return False
        app.attach_system_status_snapshot_owner(system_status_snapshot_module.SystemStatusSnapshotOwner(
            build_core=app.system_status_core_payload,
            build_advanced=app.system_status_advanced_payload,
            on_diagnostic=app.report_system_status_snapshot_failure,
        ))
        return self.snapshot.start()
    def stop_system_status_snapshot_owner(self, app) -> None:
        owner = self.snapshot
        if owner is not None:
            owner.stop()
    def report_system_status_snapshot_failure(self, app, slot: str, error: BaseException) -> None:
        """Record a failed snapshot build where the diagnostics reader can see it.

        The slot counts its own failures, but a counter inside the producer is not propagation, so
        the failure also lands in the server log ring the Logs panel reads.
        """

        emit_server_log(
            "error",
            "system-status-snapshot",
            f"{slot} snapshot build failed: {type(error).__name__}: {error}",
            dedupe_key=f"system-status-snapshot:{slot}",
            dedupe_seconds=30.0,
        )
    def system_status_snapshot_response(self, app, *, advanced: bool = False) -> tuple[bytes, Mapping[str, Any]]:
        """The route's whole job: one read of the published body, or one typed refusal.

        Returns pre-encoded bytes and their product metadata so the request thread neither
        assembles, nor encodes, nor deep-copies the ~70 KB body it is about to write.
        """

        owner = self.snapshot
        if owner is None:
            # No owner armed in this process. This is a real state - a unit-test app, or a server
            # torn down mid-request - and it is reported as one rather than silently rebuilt. It
            # goes through the SAME refusal shape as every other unpublished read, with its own
            # reason code, so a client has one thing to parse instead of two.
            refusal = system_status_snapshot_module.owner_unattached_read().refusal_payload(
                cadence_seconds=system_status_snapshot_module.SNAPSHOT_CADENCE_SECONDS,
                deadline_seconds=system_status_snapshot_module.FRESHNESS_DEADLINE_SECONDS,
            )
            body = system_status_snapshot_module.encode_snapshot_body(refusal)
            return body, common.inline_json_product_metadata(body)
        slot = owner.advanced if advanced else owner.core
        result = owner.read_advanced() if advanced else owner.read_core()
        if result.snapshot is not None:
            return result.snapshot.body, result.snapshot.product
        body = system_status_snapshot_module.encode_snapshot_body(result.refusal_payload(
            cadence_seconds=slot.cadence_seconds,
            deadline_seconds=slot.deadline_seconds,
        ))
        return body, common.inline_json_product_metadata(body)


def composed_owner_for(app: "TmuxWebtermApp", name: str, factory: Callable[["TmuxWebtermApp"], Any]) -> Any:
    """Lazily compose an owner while retaining state assigned before app initialization."""
    owner = app.__dict__.get(name)
    if owner is None:
        owner = factory(app)
        app.__dict__[name] = owner
    return owner
system_status_projector_for = partial(composed_owner_for, name="_system_status_projector", factory=SystemStatusProjector)


class TmuxWebtermApp:
    def __init__(self, sessions: list[str], dangerously_yolo: bool = False, *, status_service_mode: bool = False):
        self.sessions = sessions
        self.session_reservation_lock = threading.Lock()
        self.session_reservation_generation = 0
        self.dangerously_yolo = dangerously_yolo
        self.status_service_mode = status_service_mode
        self.host_identity = current_host_identity()
        self.state_dir = common.STATE_DIR
        self.tmux_ai_status_path = self.host_identity.namespaced_path(self.state_dir, "tmux-AI-status.json")
        self.metadata_cache = MetadataCache()
        self.chat_store = ChatStore(default_chat_database_path())
        self.chat_service = ChatService(
            self.chat_store,
            cursor_secret_path=default_chat_cursor_secret_path(),
            retention_days=self.chat_retention_days,
        )
        # Shared login throttle: one WAL SQLite file under the state dir enforces the
        # policy across every port pointing here. Admission runs before PBKDF2 in the
        # auth mixin; policy overrides are validated at load and fall back to defaults.
        self.login_rate_limiter = LoginRateLimiter(
            default_login_throttle_database_path(common.STATE_DIR),
            policy=load_login_rate_policy(common.CONFIG_DIR / LOGIN_THROTTLE_OVERRIDE_NAME),
        )
        # Optional, OFF-BY-DEFAULT attack-response escalation (defense in depth, not the
        # core 429). The edge controller only ever spawns a firewall process when an
        # operator enables it; disabled, block() is a no-op. See login_escalation.py.
        self.login_edge_controller = EdgeBlockController(runner=default_edge_runner, enabled=False)
        # DOIT.58 Phase 1: per-session/window user+agent activity ledger (heartbeat-coalesced
        # typed-time). Constructor defaults today; Preferences exposure is a deferred follow-up.
        self.activity_ledger = ActivityLedger(
            ACTIVITY_PATH,
            heartbeat_path=ACTIVITY_HEARTBEATS_PATH,
            host_identity=self.host_identity,
        )
        self.activity_ledger.load()
        self.activity_heartbeat_next_rotate_at = 0.0
        self.input_heartbeat_record = InputHeartbeatRecord()
        self._session_files_coordinator = SessionFilesCoordinator(self)
        self.agent_window_git_inventory_cache: dict[str, tuple[int, float, dict[str, Any] | None]] = {}
        self.agent_window_git_inventory_cache_lock = threading.Lock()
        self.status_pane_classification_cache: dict[str, dict[str, Any]] = {}
        self._activity_cache = ActivityCache(self)
        self._watch_bridge = WatchBridge(self)
        self.tmux_signal_cache = TtlCache(TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS, max_entries=1)
        self.tmux_signal_event_watcher: TmuxSignalEventWatcher | None = None
        self.client_watch_service.tmux_signal_payload: dict[str, Any] | None = None
        self.tmux_snapshot_history_lock = threading.RLock()
        self.tmux_snapshot_history_signatures: dict[tuple[str, str, int], tuple[int, int]] = {}
        # last-logged watched-PR truncation state, so the cap is logged only when it changes.
        self.metadata_warm_lock = threading.Lock()
        self.metadata_warm_record = MetadataWarmRecord()
        self.metadata_badge_lock = threading.Lock()
        self.metadata_badge_records: dict[str, MetadataBadgeRecord] = {}
        self.stats_collection_state = StatsCollectionState()
        self.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()
        # M8: the live retained-health store, attached by whoever started this port's
        # observer. `None` until then, and the projection says `observer_unattached`
        # rather than publishing zeros. See `attach_backend_health_store`.
        self._system_status_projector = SystemStatusProjector(self)
        self.job_client = JobClient()
        # Pins the jobd broker up for the duration of an fs-batch/differ browser interaction so a
        # saturated gate cannot idle-shut the broker between two /api/fs/batch calls (W15 #4).
        self.jobd_fs_batch_lease = JobdInteractionLease(self.job_client)
        self.jobd_operation_service = JobdOperationService()
        self.upload_retention_sweeper = UploadRetentionSweeper()
        self.approval_client = ApprovalClient()
        self.status_client = StatusClient()
        self.watch_client = WatchClient()
        self.watchd_operation_products_lock = threading.RLock()
        self.watchd_operation_products: collections.OrderedDict[str, tuple[dict[str, Any], bytes]] = collections.OrderedDict()
        self.attention_ack_lock = threading.RLock()
        self.attention_ack_keys: dict[str, float] = {}
        self.agent_window_transition_lock = threading.RLock()
        self.agent_window_transition_state: dict[str, dict[str, float | str]] = {}
        self.performance_record_lock = threading.RLock()
        self.performance_records: collections.deque[dict[str, Any]] = collections.deque(maxlen=PERFORMANCE_RECORD_LIMIT)
        self.performance_capture_records: collections.deque[dict[str, Any]] = collections.deque(maxlen=PERFORMANCE_CAPTURE_RECORD_LIMIT)
        self.performance_capture_record_count_total = 0
        self.queued_delivery_ledger = QueuedDeliveryLedger(
            state_path=SESSION_FILES_OPERATION_STATE_PATH,
        )
        self.queued_delivery_compaction_owner = QueuedDeliveryCompactionOwner(
            self.queued_delivery_ledger,
            self.submit_queued_delivery_compaction,
            self.job_client.result,
        )
        self.background_refresh_event_log_lock = threading.Lock()
        self.background_refresh_event_log_records: dict[tuple[str, str], BackgroundRefreshEventLogRecord] = {}
        self.replayed_background_client_event_ids: set[str] = set()
        # The monotonic deadline until which the client-event loop drains indexd's buffered Quick Open
        # progress frames. Opened by `mark_search_progress_active` when this web process kicks a crawl,
        # extended while unfinished frames keep arriving, and left to lapse once a crawl settles.
        self.search_progress_active_until: float = 0.0
        self.client_events = ClientEventBroker()
        self.abandon_recovered_operations()
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
        self.watch_root_index = SharedWatchRootIndex(
            WATCH_INDEX_PATH,
            owner_id=self.watch_root_owner_id,
            host_identity=self.host_identity,
        )
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
        self.update_check_record = UpdateCheckRecord()
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
                "agent_status": self.collect_current_stats_agent_status,
                "service_load": self.collect_current_stats_service_load,
                "system_memory": self.collect_current_stats_system_memory,
                "agent_tokens": self.collect_current_stats_agent_tokens,
            },
            owner_generation=self.stats_current_owner_generation,
            token_cadence_seconds=self.stats_current_token_cadence_seconds,
            collector_context=self.stats_current_collector_context,
            family_cadence_seconds=self.stats_current_family_cadence_seconds,
        )
        # A persistent child owns all Quick Open builds and SQLite writes.
        # HTTP/WebSocket processes remain read-only index consumers.
        file_index.set_background_owner_checker(self.search_index_can_build)
        file_index.set_background_owner_refresh_requester(self.request_background_refresh)
        file_index.set_background_index_search_requester(self.request_background_index_search)
        file_index.set_background_owner_bytes_recorder(self.record_background_search_index_bytes_written)
        file_index.set_background_owner_done_notifier(self.publish_background_refresh_done)
        file_index.set_search_progress_notifier(self.publish_search_progress)

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

    def stats_current_collector_context(self) -> dict[str, Any]:
        """Expose the elected web identity AND where to reach it; statsd reads the rest itself.

        The control socket is part of this handshake because this process is the only one
        that authoritatively knows it. statsd used to look the address up in the background
        owner ELECTION record, which a managed instance never writes -- so its CPU/memory
        sample was produced every second and silently dropped for the life of the process.
        """

        _source_id, _label, port = self.stats_current_process_identity()
        generation = self.stats_current_owner_generation()
        if generation is None:
            raise RuntimeError("stats collector owner is unavailable")
        return {
            "pid": os.getpid(),
            "port": port,
            "owner_generation": generation,
            "control_socket": str(self.control_server.path),
        }

    def tmux_ai_status_empty(self) -> dict[str, Any]:
        return {
            "version": TMUX_AI_STATUS_VERSION,
            "rev": 0,
            "updated_at": 0.0,
            "stable_host_id": self.host_identity.stable_host_id,
            "hostname": self.host_identity.display_hostname,
            "attention_acks": {"rev": 0, "updated_at": 0.0, "keys": {}},
            "attention_instances": {"updated_at": 0.0, "instances": {}},
        }

    def _read_shared_tmux_ai_status_locked(self) -> dict[str, Any]:
        status = self.tmux_ai_status_empty()
        data = read_json_file(self.tmux_ai_status_path, {}, exceptions=(OSError, json.JSONDecodeError, TypeError, ValueError))
        if not isinstance(data, dict):
            data = {}
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
        payload["stable_host_id"] = self.host_identity.stable_host_id
        payload["hostname"] = self.host_identity.display_hostname
        self.tmux_ai_status_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.tmux_ai_status_path,
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
        if not isinstance(payload, dict):
            return None
        # statusd's completed snapshot generation is the one authoritative revision shared by
        # every agent-window row it contains. Preserve it before downstream Tabber/YO!agent
        # consumers split the payload, rather than inventing a second revision clock.
        payload["agent_window_snapshot_revision"] = max(0, int(response.get("generation") or 0))
        return payload

    @staticmethod
    def agent_window_snapshot_rows_by_target(payload: AutoApproveStatusPayload) -> tuple[int, dict[tuple[str, str, str, str], dict[str, Any]]]:
        """Return the roster-owned state rows keyed by session, pane target, and client kind."""

        try:
            revision = max(0, int(payload.get("agent_window_snapshot_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
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
                window = TmuxWebtermApp.agent_window_index_key(row.get("window_index") if row.get("window_index") is not None else row.get("window"))
                kind = str(row.get("kind") or "").lower()
                if window and target and kind:
                    rows[(str(session), window, target, kind)] = copy.deepcopy(row)
        return revision, rows

    @staticmethod
    def status_roster_recent_agent_rows(session: str, roster_rows: list[dict[str, Any]], locale: str = "en") -> list[dict[str, Any]]:
        """Build minimal Tabber rows when statusd is the only current agent source."""
        result: list[dict[str, Any]] = []
        for row in roster_rows:
            kind = str(row.get("kind") or "").lower()
            if kind not in {"claude", "codex"}:
                continue
            window = TmuxWebtermApp.agent_window_index_key(row.get("window_index") if row.get("window_index") is not None else row.get("window"))
            window_name = str(row.get("window_name") or kind)
            window_label = str(row.get("window_label") or f"{window}:{window_name}")
            state = str(row.get("state") or "idle")
            result.append({"session": session, "window": window, "window_index": int(window) if window.isdigit() else None, "window_name": window_name, "window_label": window_label, "pane": str(row.get("pane") or ""), "pane_target": str(row.get("pane_target") or ""), "agent_kind": kind, "agent_model": "", "cwd": "", "transcript": "", "recent_paths": [], "last_used_ts": 0.0, "last_used_text": "", "last_used_source": "statusd-roster", "last_used_reason": "statusd fallback", "running": state == "working", "state": state, "state_text": "", "sort_ts": float(row.get("observed_ts") or 0.0), "label": server_string(locale, "summary.recentAgentLabel", session=session, window=window_label)})
        return result

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
                # A later roster reconciliation may retain a row for diagnostics, but a pane that
                # no longer exists is never capacity or transition state.  Keep the stale marker
                # available to its owner while keeping it out of the durable status chart.
                if not isinstance(row, dict) or row.get("stale") is True:
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
        return transition_seconds > 0 and sample_time >= stopped_ts and sample_time - stopped_ts < transition_seconds

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
        pane_target = str(row.get("pane_target") or row.get("pane") or "").strip()
        kind = str(row.get("kind") or "").strip().lower()
        # A tmux window name/index is not a process identity: multiple panes can host the same
        # agent kind in one window. Keep the pane target in the shared stats/token identity so
        # status, Tabber, and durable attention all count the same physical agent window.
        parts = [session, str(window).strip(), pane_target, kind]
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
        # statusd deliberately discovers only cheap topology/status fields, so its public rows
        # cannot carry transcript paths. Token collection runs on its independent cadence and is
        # the sole consumer that needs enriched paths; keep that work out of UI status refreshes.
        if rows and not include_missing:
            unresolved_keys = sorted({
                self.stats_agent_token_key(row, index)
                for index, row in enumerate(rows)
                if not str(row.get("transcript") or "").strip()
            })
            if unresolved_keys:
                discovered_token_rows = self.stats_agent_token_enriched_rows("\n".join(unresolved_keys))
                existing_keys = {str(row["key"]) for row in token_rows}
                token_rows.extend(
                    row for row in discovered_token_rows
                    if str(row["key"]) not in existing_keys
                )
        return token_rows

    def stats_agent_token_enrich_memo(self) -> TtlCache:
        """Negative/positive memo for the transcript enrich, keyed on the unresolved-agent roster.

        Some agents can never resolve — session ``yo7770`` runs in a tree with no matching Codex
        rollout — so ``any(not row["transcript"])`` was permanently true and forced a full
        ``discover_sessions(enrich_paths=True)`` (measured 1.53-2.05s CPU) on every collector
        sample, ~17.7% of a core at the 10s watched cadence, with no way to ever improve.

        The memo is keyed on the exact set of transcript-less agent keys, so a newly started agent
        changes the key and re-enriches on the very next sample. The TTL only bounds the other
        case: an unchanged roster whose transcript appears late. It matches the idle collector
        cadence, so at worst this costs what the idle path already paid.
        """

        memo = self.__dict__.get("stats_agent_token_enrich_memo_cache")
        if memo is None:
            memo = TtlCache(
                ttl_seconds=STATS_AGENT_TOKEN_ENRICH_MEMO_TTL_SECONDS,
                max_entries=STATS_AGENT_TOKEN_ENRICH_MEMO_MAX_ENTRIES,
            )
            self.__dict__["stats_agent_token_enrich_memo_cache"] = memo
        return memo

    def stats_agent_token_enriched_rows(self, unresolved_signature: str) -> list[dict[str, Any]]:
        memo = self.stats_agent_token_enrich_memo()
        cached = memo.get_or_miss(unresolved_signature)
        if cached is not CACHE_MISS:
            return cached
        discovered_sessions, _errors = discover_sessions(self.sessions)
        discovered_rows: list[dict[str, Any]] = []
        for session, info in discovered_sessions.items():
            for agent in info.agents:
                kind = str(agent.kind or "").strip().lower()
                transcript = str(agent.transcript or "").strip()
                if kind not in {"claude", "codex"} or not transcript:
                    continue
                window, _pane = session_files.agent_window_for_info(info, agent)
                try:
                    window_index = int(window)
                except ValueError:
                    window_index = None
                discovered_rows.append({
                    "session": session,
                    "window": window,
                    "window_index": window_index,
                    "window_label": f"{window}:{kind}" if window else kind,
                    "kind": kind,
                    "transcript": transcript,
                })
        discovered_token_rows = self.stats_agent_token_rows(discovered_rows) if discovered_rows else []
        memo.set(unresolved_signature, discovered_token_rows)
        return discovered_token_rows

    def stats_current_owner_generation(self) -> int | None:
        if not self.background_can_run(BACKGROUND_ROLE_STATS_SAMPLER):
            return None
        started_at_ns = self.background_owner.owner_payload().get("started_at_ns")
        if isinstance(started_at_ns, bool) or not isinstance(started_at_ns, int):
            return None
        return started_at_ns if started_at_ns >= 0 else None

    def stats_current_token_cadence_seconds(self) -> float:
        return STATS_AGENT_TOKEN_SAMPLE_SECONDS if self.client_events.has_demand("stats") else STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS

    def stats_current_family_cadence_seconds(self, family: str) -> float:
        spec = stats_current_families.FAMILY_BY_NAME[family]
        cadence = spec.cadence_seconds(watched=self.client_events.has_demand("stats"))
        if cadence is None:
            raise ValueError(f"{family} is not a scheduled stats collector")
        return cadence

    def record_current_browser_observations(
        self,
        body: bytes,
        *,
        authenticated_username: str,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        """Forward one bounded browser batch to statsd without decoding it."""

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
        response = self.stats_current_client.append(
            browser_upload=body,
            authenticated_username=authenticated_username,
        )
        if response.get("ok") is not True:
            if (
                response.get("status") == "upgrade_required"
                or response.get("error_code") == "upgrade_required"
            ):
                return response, HTTPStatus.UPGRADE_REQUIRED
            if response.get("status") == "unsupported":
                return response, HTTPStatus.BAD_REQUEST
            return {"ok": False, "status": "unavailable", "reason": "statsd unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE
        return {
            "ok": True,
            "source_generation": response.get("source_generation", 0),
            "accepted": response.get("accepted", 0),
            "duplicates": response.get("duplicates", 0),
            "observation_receipts": response.get("observation_receipts", []),
        }, HTTPStatus.OK

    # `collect_current_stats_cpu` used to live here. It was NEVER registered in the collector
    # registry above, so it had no production call site and only two tests referenced it -- which
    # is why the "no push has landed yet, append nothing" guard it carried never ran, and statsd's
    # real producer (`StatsCurrentService._collect_host_facts_if_due`) went on writing a fabricated
    # first `0.0`. The guard now lives at the root owner, `host_collectors.CpuSampler.sample`, in
    # the one process that knows whether it had a baseline. A second, unreachable CPU producer in
    # the web process is what made the defect invisible, so it is not kept.

    def collect_current_stats_agent_status(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        status_payload = self.status_snapshot_payload()
        rows = self.stats_agent_window_rows_from_auto_approve_payload(status_payload) if status_payload is not None else []
        snapshot_revision = max(0, int(status_payload.get("agent_window_snapshot_revision") or 0)) if status_payload is not None else 0
        states: dict[str, str] = {}
        session_states: dict[str, str] = {}
        seen_keys: set[str] = set()
        transition_seconds = self.notification_transition_seconds()
        with self.stats_collection_state.agent_activity_lock:
            for index, row in enumerate(rows):
                key = self.stats_agent_token_key(row, index)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                state = self.stats_agent_activity_kind_locked(
                    row,
                    key,
                    attempt.scheduled_at,
                    transition_seconds,
                )
                states[key] = state
                session = str(row.get("session") or "").strip()
                if session and (session not in session_states or STATS_AGENT_SESSION_STATE_PRIORITY[state] < STATS_AGENT_SESSION_STATE_PRIORITY[session_states[session]]):
                    session_states[session] = state
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
            session_states=session_states,
            snapshot_revision=snapshot_revision,
        )

    # `collect_current_stats_gpu` used to live here, unregistered and unreferenced in the same way
    # and for the same family statsd already collects inline from `host_collectors.gpu_devices()`.
    # It is removed with the CPU one: an unwired second producer cannot be verified and is exactly
    # where a guard goes to be silently skipped.

    def collect_current_stats_service_load(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        # The CPU family is the single owner of the yolomux.py web-process metric, so this
        # family must never carry a "web" row and render one PID twice at two cadences. That
        # exclusion is now structural rather than a filter here: LocalServicesCollector
        # rejects any producer outside the six-service inventory, so a "web" row cannot reach
        # this loop at all. The filter that used to sit here matched a row the projection
        # could not produce.
        #
        # This periodic sampler and the System projection read the SAME collector snapshot.
        # It used to call runtime_local_services() and re-derive running/cpu/rss out of the
        # rendered HTTP payload, which meant a second parse of a projection it did not own;
        # the typed rows already carry exactly these three fields.
        samples = []
        for row in self.local_services_snapshot().rows:
            samples.append(stats_current_collectors.ServiceLoadSample(
                row.service,
                row.running,
                max(0.0, row.cpu_percent) if row.cpu_percent is not None else 0.0,
                max(0.0, float(row.rss_bytes)) if row.running and row.rss_bytes is not None else None,
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
        macos_snapshot = current_darwin_system_memory_snapshot()
        memory = macos_snapshot[0] if macos_snapshot is not None else current_system_memory_bytes()
        if memory is None:
            raise RuntimeError("system memory metrics unavailable")
        macos_details = None if macos_snapshot is None else macos_snapshot[1]
        return stats_current_collectors.system_memory_success(
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            source_id="host",
            used_bytes=float(memory[1]),
            capacity_bytes=float(memory[0]),
            macos_details=None if macos_details is None else asdict(macos_details),
        )

    def collect_current_stats_agent_tokens(
        self,
        attempt: Any,
    ) -> stats_current_collectors.CollectorFacts:
        rows = self.stats_agent_window_rows()
        if self.sessions and not rows:
            # statusd owns this roster. During a refresh it can be briefly
            # unavailable; emitting no facts preserves that unknown interval
            # without treating it as measured zero token usage.
            return stats_current_collectors.CollectorFacts()
        atoms = []
        tombstones = []
        scan = self.stats_current_transcript_usage.scan(self.stats_agent_token_rows(rows))
        current_settings = self.settings_payload().get("settings", {})
        rejection_reasons: dict[str, int] = {}
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
            except stats_current_usage.UsageValidationError as error:
                reason = str(error)[:160] or "usage_validation_error"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue
        if "stats_current_client" in self.__dict__ and isinstance(self.stats_current_client, StatsCurrentClient):
            self.stats_current_client.set_usage_atom_backfill_status(
                self.stats_current_transcript_usage.usage_atom_backfill_status_for_scan(
                    scan,
                    atoms_accepted=len(atoms),
                    rejection_reasons=rejection_reasons,
                )
            )
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
        """Read the last scheduler-owned CPU sample without collecting in an API thread.

        The three sampled fields are ABSENT (``None``) until statsd pushes, never ``0``.
        `stats_cpu_sample` is their only writer, so before the first push nothing has
        measured this process -- and the previous hand-built record answered "is this
        value absent, or is it zero?" with a confident zero that reached the Daemons
        panel stamped ``measured`` and was summed into the roster's Memory total while a
        real ~160MB RSS went missing from it. ``pid`` and ``started_at`` are read here
        directly, not sampled, so they stay real.

        There is no ``uptime_seconds`` in this record any more. It used to be synthesized
        here and stamped onto every push, which made the panel's uptime a CACHED value: it
        froze at the last delivered sample -- still labeled ``measured`` -- exactly when
        delivery broke. It is derived at render time from this process's own clock in
        ``system_status_server_block``, which is now its only owner.
        """

        with self.stats_collection_state.sample_lock:
            cached = self.stats_collection_state.sample_record.cached_payload
            if cached is not None:
                return dict(cached)
        return {
            # No `time`: a push timestamp would be a fourth fabricated fact, and every reader
            # here already treats an absent/zero `time` as "never pushed". Synthesizing one made
            # this record look freshly delivered to the CPU-budget reader.
            "pid": os.getpid(), "started_at": SERVER_STARTED_AT,
            "cpu_percent": None,
            "system_cpu_percent": None, "rss_bytes": None,
            "reason_code": STATS_SAMPLE_NOT_PUSHED_REASON_CODE,
            "reason": STATS_SAMPLE_NOT_PUSHED_REASON,
        }

    def current_stats_sample(self, *, force: bool = False) -> tuple[dict[str, Any], bool]:
        """Compatibility reader: statsd pushes the sole sample into this cache."""

        return self.latest_stats_sample(), False

    def start_background_owner(self, port: int | None = None, priority: int = 0, *, managed_instance: bool = False) -> bool:
        if managed_instance:
            # The launcher allocated this root from the port before importing the
            # product, so all background work is private to this process.  Keep
            # the owner API intact for callers while bypassing same-root election.
            self.background_owner = DisabledBackgroundOwner(port=port, project_root=str(PROJECT_ROOT))
            file_index.set_background_owner_checker(self.search_index_can_build)
            self.background_owner.start()
            self.handle_background_owner_acquired({"last_transition": "local", "generation": self.background_owner.owner_payload()})
            return True
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
        if not acquired:
            self.replay_shared_background_client_events()
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
        composed_owner_for(self, "_session_files_coordinator", SessionFilesCoordinator).start()
        self.pricing_refresh_coordinator.start_periodic()
        self.stats_current_runtime.start()
        self.refresh_search_indexer_schedule()
        self.warm_start_session_files_payload_cache()
        self.warm_start_tabber_activity_cache()
        self.start_tabber_activity_cache_warmer()
        self.publish_background_client_event("background_owner_changed", self.background_owner.status_payload(), trigger="background-owner", cache="ready")

    def refresh_search_indexer_schedule(self) -> dict[str, Any]:
        """Lease indexd and enqueue startup-depth-1 work for every configured indexed root (item 1).

        Only the elected background owner leases and schedules. Called on owner acquisition and
        whenever indexed-root settings change while this server owns scheduling, so adding a root
        starts its layer-1 crawl proactively and removing every root releases the lease and lets the
        daemon idle out honestly. Reuses the one `indexd` service; it starts no second scheduler.
        """
        if not self.background_owner.is_owner():
            return {"ok": True, "owner": False, "scheduled_roots": [], "leased": False}
        settings = self.settings_payload().get("settings", {})
        file_explorer = settings.get("file_explorer", {}) if isinstance(settings, dict) else {}
        roots = list(self.indexed_repo_discovery_dirs(file_explorer))
        result = self.search_indexer.lease_configured_roots(roots)
        return {**result, "owner": True}

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

    def performance_diagnostics_payload(self, measurement_scope: str = "") -> dict[str, Any]:
        """Return bounded profiling summaries without making status polling expensive."""

        metrics = self.performance_metrics_payload(measurement_scope=measurement_scope)
        browser_diagnostics_response = self.stats_current_client.browser_diagnostics()
        browser_profiles = {
            key: value for key, value in browser_diagnostics_response.get("profiles", {}).items()
            if key != "ok"
        }
        browser_observation_status = {
            key: value for key, value in browser_diagnostics_response.get("observation_status", {}).items()
            if key != "ok"
        }
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
            "transport": local_services_registry.transport_diagnostics(),
            "shared_phase_counters": phase_rows[:64],
            "repeated_work": repeated_work[:64],
            "browser_profiles": browser_profiles,
            "browser_observation_status": browser_observation_status,
            **self.queued_delivery_ledger.diagnostics(),
        }

    def observe_http_commit(self, payload: object, status: HTTPStatus | int) -> None:
        """Register an accepted/committed operation's queued state before its response flush."""

        self.queued_delivery_ledger.observe_http_commit(payload, status)

    def observe_http_receipt(self, payload: object, status: HTTPStatus | int) -> None:
        """Record that an accepted receipt reached the client, only after a successful flush."""

        self.queued_delivery_ledger.observe_http_receipt(payload, status)

    def observe_http_product_delivery(self, key: str, epoch: int) -> None:
        """Register one explicit ready-byte terminal before the response is framed."""

        self.queued_delivery_ledger.observe_ready_product(key, epoch)

    @staticmethod
    def new_api_request_id() -> str:
        return f"r-{uuid.uuid4().hex}"

    @staticmethod
    def operation_ready_result(
        request_id: str,
        data: dict[str, Any],
        *,
        quality: dict[str, Any] | None = None,
        warnings: list[Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": "ready",
            "request": {"id": str(request_id)},
            "data": copy.deepcopy(data),
            "quality": copy.deepcopy(quality or {"complete": True, "stale": False}),
            "warnings": copy.deepcopy(warnings or []),
        }

    # `operation_failed_result` used to live here: an unreferenced second constructor of the
    # `state: failed` envelope that spread a caller dict straight into the result, so it could
    # produce a canonical failure with no causal stack and no producer would notice.  Canonical
    # failures are built only by `common.error_payload(canonical=True)`, which validates the stack.

    @staticmethod
    def typed_filesystem_operation_failure(failure: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus] | None:
        job = failure.get("job") if isinstance(failure.get("job"), dict) else failure
        details = job.get("failure") if isinstance(job.get("failure"), dict) else job
        filesystem_error = details.get("filesystem_error") if isinstance(details.get("filesystem_error"), dict) else None
        if filesystem_error is None:
            return None
        try:
            status = HTTPStatus(int(details.get("status") or filesystem_error.get("status") or HTTPStatus.BAD_REQUEST))
        except (TypeError, ValueError):
            status = HTTPStatus.BAD_REQUEST
        terminal_error = copy.deepcopy(filesystem_error)
        terminal_error["terminal"] = True
        return terminal_error, status

    @staticmethod
    def refused_filesystem_operation_request(
        operation: str,
        path: str,
        args: dict[str, Any],
    ) -> tuple[dict[str, Any], HTTPStatus] | None:
        """Refuse a filesystem request the web thread can already prove the worker will reject.

        Acceptance is what makes this necessary rather than cosmetic.  A single filesystem request
        is answered with a 202 receipt and completed in the jobd operation pool, so a descriptor
        that cannot succeed -- an empty, relative or NUL/newline path, or a rename to an empty or
        illegal child name -- would otherwise reserve a bounded completion slot, submit a job, and
        terminalize `invalid_request` after the caller has already read its response.  That
        terminal failure is recorded as an operator-visible server error nothing can correlate
        back to the request that caused it.

        These are the only two refusals decidable without a descriptor, so they are the whole
        class.  Both rules are applied through their existing owners -- the worker reaches
        `validate_request_path_lexical` through `parsed_request_path`/`safe_path`/`safe_parent` and
        calls `validated_child_name` from `rename_path` -- so refusal and execution cannot
        disagree, and the refusal payload is identical to the typed failure the worker would have
        produced.  `jobd._filesystem_operation_untyped` coerces `new_name` with `str(... or "")`,
        so the same coercion is applied here rather than a second reading of the same argument.

        Acceptance calls the LEXICAL owner only, never `parsed_request_path`.  Expanding `~user`
        is an NSS/passwd lookup that can block on a networked passwd source, and the web process
        answers every request on this thread, so one stalled lookup would stall all of them.
        """

        try:
            filesystem.validate_request_path_lexical(path)
            if operation == "rename":
                filesystem.validated_child_name(str(args.get("new_name") or ""))
        except filesystem.FilesystemError as error:
            refusal = dict(error.payload(path=path))
            refusal["terminal"] = True
            return refusal, HTTPStatus(int(error.status))
        return None

    @staticmethod
    def typed_filesystem_operation_failed_result(
        request_id: str,
        filesystem_error: dict[str, Any],
        status: HTTPStatus,
        *,
        route: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Frame a worker-owned filesystem failure for terminal HTTP replay."""

        descriptor = filesystem_error.get("user_message")
        if not isinstance(descriptor, dict):
            descriptor = {}
        status_codes = {
            # `FilesystemError.os_error` raises 403 for a PermissionError and nothing else does, so
            # naming it is what makes a denied read distinguishable from a request the worker judged
            # malformed.  Without this it fell through to `invalid_request`, which told an operator
            # the browser had sent something illegal when the file was simply not readable.
            HTTPStatus.FORBIDDEN: "permission_denied",
            HTTPStatus.NOT_FOUND: "path_not_found",
            HTTPStatus.CONFLICT: "conflict",
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "request_too_large",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
        }
        code = status_codes.get(status, "invalid_request" if status < 500 else "dependency_failed")
        diagnostic = str(filesystem_error.get("error") or "")
        return common.error_payload(
            descriptor.get("fallback") or diagnostic or "filesystem operation failed",
            message_key=str(descriptor.get("key") or "common.requestFailed"),
            message_params=descriptor.get("params") if isinstance(descriptor.get("params"), dict) else {},
            canonical=True,
            code=code,
            origin="local_services.jobd",
            retryable=False,
            details={
                "status": int(status),
                "path": str(filesystem_error.get("path") or ""),
                "operation_id": operation_id,
                "diagnostic": diagnostic,
            },
            stack=[
                {
                    "component": "server.http",
                    "operation": route,
                    "code": "dependency_failed",
                },
                {
                    "component": "local_services.jobd",
                    "operation": "jobd.result",
                    "code": code,
                },
            ],
            request_id=request_id,
        )

    @classmethod
    def session_files_ready_result(
        cls,
        request_id: str,
        payload: SessionFilesPayload,
    ) -> dict[str, Any]:
        data = copy.deepcopy(payload)
        cache = data.get("cache") if isinstance(data.get("cache"), dict) else {}
        warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
        return cls.operation_ready_result(
            request_id,
            data,
            quality={
                "complete": not bool(data.get("partial")),
                "stale": bool(cache.get("stale")),
            },
            warnings=warnings,
        )

    @staticmethod
    def local_service_operation_failure_result(
        request_id: str,
        failure: dict[str, Any],
        *,
        service: str,
        route: str,
        operation_id: str = "",
        operation: str = "request",
        code: str = "service_unavailable",
    ) -> dict[str, Any]:
        service_name = str(service).strip()
        message = str(failure.get("error") or failure.get("reason") or f"{service_name} product producer unavailable")
        cause = failure.get("cause")
        if not isinstance(cause, dict):
            cause = failure.get("failure") if isinstance(failure.get("failure"), dict) else {}
        root = {
            "component": f"local_services.{service_name}",
            "operation": str(operation),
            "code": str(code),
        }
        exception = cause.get("exception") if isinstance(cause, dict) else None
        frames = cause.get("frames") if isinstance(cause, dict) else None
        if isinstance(exception, dict):
            root["exception"] = copy.deepcopy(exception)
        if isinstance(frames, list):
            root["frames"] = copy.deepcopy(frames)
        return common.error_payload(
            message,
            message_key="common.requestFailed",
            canonical=True,
            code=code,
            origin=f"local_services.{service_name}",
            retryable=False,
            details={
                "service": service_name,
                "operation_id": str(operation_id),
                "reason": str(failure.get("_transport_error") or failure.get("status") or code),
            },
            stack=[
                {
                    "component": "server.http",
                    "operation": str(route),
                    "code": "dependency_failed",
                },
                root,
            ],
            request_id=request_id,
        )

    @classmethod
    def jobd_operation_failure_result(
        cls,
        request_id: str,
        failure: dict[str, Any],
        *,
        route: str,
        operation_id: str = "",
        operation: str = "jobd.request",
        code: str = "service_unavailable",
    ) -> dict[str, Any]:
        return cls.local_service_operation_failure_result(
            request_id,
            failure,
            service="jobd",
            route=route,
            operation_id=operation_id,
            operation=operation,
            code=code,
        )

    @classmethod
    def session_files_failure_result(
        cls,
        request_id: str,
        failure: dict[str, Any],
        *,
        operation_id: str = "",
        operation: str = "jobd.request",
        code: str = "service_unavailable",
    ) -> dict[str, Any]:
        return cls.jobd_operation_failure_result(
            request_id,
            failure,
            route="GET /api/session-files",
            operation_id=operation_id,
            operation=operation,
            code=code,
        )

    def record_operation_failure(self, operation_id: str, result: dict[str, Any]) -> None:
        """Record one terminal operation failure at the severity its cause earns.

        An accepted operation is completed off the request thread, so the caller's outcome is
        recorded here rather than by ``write_api_response``.  Browsing to a directory that no
        longer exists is an ordinary outcome of that browsing -- a 404 to one caller, nothing an
        operator can act on -- and recording it as an error fills release-blocking evidence with
        rows produced by correct operation.  ``failure_record_level`` is the one owner of that
        distinction and the synchronous writer asks it the same question; a genuine dependency
        failure, an abandoned producer and a malformed failure record all still record an error.
        """

        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        service = str(details.get("service") or "jobd")
        emit_server_log(
            failure_record_level(error),
            f"{service}-operation",
            json.dumps({
                "request": result.get("request"),
                "operation": {"id": str(operation_id)},
                "code": error.get("code"),
                "origin": error.get("origin"),
                "stack": error.get("stack"),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            category="operation",
            dedupe_key=f"{service}-operation:{operation_id}",
            dedupe_seconds=5.0,
        )

    def terminalize_operation(
        self,
        operation_id: str,
        result: dict[str, Any],
        status: HTTPStatus | int,
    ) -> dict[str, Any] | None:
        event = self.queued_delivery_ledger.terminalize_operation(operation_id, result, status)
        if event is None:
            return None
        if str(result.get("state") or "") == "failed":
            self.record_operation_failure(operation_id, result)
        self.publish_client_event(
            "operation_terminal",
            event,
            trigger="operation-terminal",
            cache="ready",
        )
        return event

    def abandon_recovered_operations(self) -> None:
        for record in self.queued_delivery_ledger.open_operations():
            operation_id = str(record.get("id") or "")
            request_id = str(record.get("request_id") or self.new_api_request_id())
            result = self.jobd_operation_failure_result(
                request_id,
                {
                    "error": "accepted producer was abandoned when the server instance stopped",
                    "status": "producer_abandoned",
                },
                route=str(record.get("route") or "GET /api/operations/{id}"),
                operation_id=operation_id,
                operation="jobd.result",
                code="producer_abandoned",
            )
            self.queued_delivery_ledger.terminalize_operation(
                operation_id,
                result,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def operation_status_payload(self, operation_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        status = self.queued_delivery_ledger.operation_status(operation_id)
        if status is not None:
            return status
        request_id = self.new_api_request_id()
        return common.error_payload(
            "operation not found",
            message_key="common.notFound",
            canonical=True,
            code="operation_not_found",
            origin="server.http",
            retryable=False,
            details={"operation_id": str(operation_id)},
            stack=[{
                "component": "server.http",
                "operation": "GET /api/operations/{id}",
                "code": "operation_not_found",
            }],
            request_id=request_id,
        ), HTTPStatus.NOT_FOUND

    def operation_replay_payload(self, operation_id: str) -> dict[str, Any] | None:
        return self.queued_delivery_ledger.operation_replay_event(operation_id)

    def acknowledge_operation_delivery(self, operation_id: str, cursor: dict[str, Any]) -> bool:
        return self.queued_delivery_ledger.acknowledge_operation_delivery(operation_id, cursor)

    def acknowledge_operation_deliveries(self, acknowledgments: list[dict[str, Any]]) -> tuple[dict[str, Any], HTTPStatus]:
        acknowledged = self.queued_delivery_ledger.acknowledge_operation_deliveries(acknowledgments)
        acknowledged_set = set(acknowledged)
        return {
            "ok": True,
            "acknowledged": acknowledged,
            "ignored": [item["id"] for item in acknowledgments if item["id"] not in acknowledged_set],
        }, HTTPStatus.OK

    def submit_queued_delivery_compaction(self, state_path: Path, coalesce_key: str) -> dict[str, Any]:
        response, _body = self.job_client.produce(
            "queued_delivery_compact",
            {"state_path": str(state_path)},
            priority="maintenance",
            generation=1,
            coalesce_key=coalesce_key,
            delivery="receipt",
            fresh_only=True,
        )
        return response

    def operation_access_allowed(self, operation_id: str, sessions: list[str]) -> bool:
        context = self.queued_delivery_ledger.operation_context(operation_id)
        if context is None:
            return False
        operation_session = str(context.get("session") or "")
        return bool(operation_session and operation_session in {str(session) for session in sessions})

    def wait_for_jobd_operations_terminal(self, timeout: float) -> None:
        """Keep the completion owner live until every accepted operation is terminal."""

        settled = self.jobd_operation_service.wait_for_idle(timeout)
        open_operations = self.queued_delivery_ledger.open_operations()
        if not settled or open_operations:
            raise AssertionError({
                "accepted_operations_settled": settled,
                "open_operations": open_operations,
            })

    def stop_jobd_operation_service(self) -> None:
        self.queued_delivery_compaction_owner.stop()
        self.jobd_operation_service.stop()

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
        composed_owner_for(self, "_session_files_coordinator", SessionFilesCoordinator).stop()
        self.job_client.stop_for_scheduler()
        with self.metadata_warm_lock:
            self.metadata_warm_record.stop_event.set()
        composed_owner_for(self, "_activity_cache", ActivityCache).demote()
        # Release the configured-root scheduler lease so the daemon may idle out honestly and its
        # Daemons row stops reporting a scheduled obligation this demoted server no longer owns.
        self.search_indexer.release_scheduler_lease()
        file_index.clear_memory_indexes()
        # Demotion/release is just as relevant to followers as acquisition.  Use
        # the durable background fan-out parent so clients on another port do
        # not keep displaying an owner that has already stopped its workers.
        self.publish_background_client_event("background_owner_changed", self.background_owner.status_payload(), trigger="background-owner", cache="ready")

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
        # The single classifier owns "must the caller compute locally?"; every
        # consumer routes through it so no two derive contradictory verdicts.
        return RefreshOutcome.from_result(result).fallback

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
                    elif request_payload.get("operation") == "promote":
                        # Item 5: a Quick Open query for a not-yet-covered scope promotes that root's
                        # existing frontier to user-visible-demand, never launching a second crawl.
                        result["indexer"] = self.search_indexer.promote_user_visible(
                            root, str(request_payload.get("directory") or "")
                        )
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
                    if result["indexer"].get("accepted"):
                        # A crawl was accepted in the daemon; become the follower that drains its
                        # redacted progress frames onto the shared bus while it runs.
                        self.mark_search_progress_active()
                    if not result["indexer"].get("accepted"):
                        result.update({
                            "ok": False,
                            "accepted": False,
                            "error": str(result["indexer"].get("error") or "persistent indexer unavailable"),
                        })
                except filesystem.FilesystemError as exc:
                    result.update({"ok": False, "accepted": False, "error": str(exc)})
        # Classify the raw result ONCE, on ingress. Every downstream decision --
        # the performance-sample label, the owner/follower role, the fallback
        # branch, and `refreshing_elsewhere` -- reads this single verdict instead
        # of re-inspecting the raw booleans, so they cannot diverge. Stamp the
        # derived `refreshing_elsewhere` so control-outcome consumers reading the
        # returned dict (e.g. `_unindex_safe_root`) get the same judgement.
        outcome = RefreshOutcome.from_result(result)
        result["refreshing_elsewhere"] = outcome.refreshing_elsewhere
        self.record_performance_sample(
            role,
            "background-refresh-request",
            trigger=str(request_payload.get("reason") or result.get("role") or ""),
            compute_ms=(time.perf_counter() - started) * 1000,
            payload=request_payload,
            cache_key=request_payload.get("cache_key", role),
            cache_status=outcome.cache_status,
            owner_role="owner" if outcome.local_owner else "follower",
            details={"accepted": outcome.accepted, "fallback": outcome.fallback, "coalesced": outcome.coalesced},
        )
        if outcome.local_owner:
            if role == BACKGROUND_ROLE_STATS_SAMPLER and request_payload.get("family") == "agent_tokens":
                result["refreshing"] = self.stats_current_runtime.wake("agent_tokens")
            if not outcome.coalesced:
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
        elif outcome.fallback:
            self.record_background_fallback(role, result, payload)
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
            return []
        return [error]

    def rotate_activity_heartbeats_if_due(self, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else float(now)
        if moment < self.activity_heartbeat_next_rotate_at:
            return 0
        kept = self.activity_ledger.rotate_heartbeats()
        self.activity_heartbeat_next_rotate_at = moment + SERVER_ACTIVITY_HEARTBEAT_ROTATE_SECONDS
        return kept

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
            manifest = read_json_file(BACKGROUND_CLIENT_EVENTS_PATH, {}, exceptions=(OSError, json.JSONDecodeError, TypeError))
            raw_events = manifest.get("events") if isinstance(manifest, dict) else []
            events = [item for item in raw_events if isinstance(item, dict)] if isinstance(raw_events, list) else []
            resource = client_event_resource(event_type, payload)
            # The manifest is a bounded recovery snapshot, not an audit log. Retain only the
            # newest event for each independently ordered resource so a returning follower repairs
            # current state once instead of replaying stale transitions.
            events = [item for item in events if client_event_resource(str(item.get("type") or ""), item.get("payload") if isinstance(item.get("payload"), dict) else {}) != resource]
            events.append(record)
            events = events[-BACKGROUND_CLIENT_EVENT_MANIFEST_LIMIT:]
            payload_text = json.dumps({"version": 1, "events": events}, sort_keys=True, separators=(",", ":")) + "\n"
            atomic_write_text(BACKGROUND_CLIENT_EVENTS_PATH, payload_text, mode=0o600)
        return record

    def replay_shared_background_client_events(self) -> int:
        """Replay the durable latest-per-resource manifest after a follower was offline."""
        with file_lock(BACKGROUND_CLIENT_EVENTS_PATH, dir_mode=0o700):
            manifest = read_json_file(BACKGROUND_CLIENT_EVENTS_PATH, None, exceptions=(OSError, json.JSONDecodeError, TypeError))
            if manifest is None:
                return 0
        raw_events = manifest.get("events") if isinstance(manifest, dict) else []
        if not isinstance(raw_events, list):
            return 0
        replayed = 0
        for record in raw_events:
            if not isinstance(record, dict):
                continue
            event_id = str(record.get("id") or "")
            event_type = str(record.get("type") or "")
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if not event_id or event_id in self.replayed_background_client_event_ids:
                continue
            if event_type not in BACKGROUND_CLIENT_EVENT_TYPES or event_type not in CLIENT_EVENT_TYPES:
                continue
            self.replayed_background_client_event_ids.add(event_id)
            self.handle_background_client_event({"event_type": event_type, "payload": payload})
            replayed += 1
        return replayed

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

    def publish_search_progress(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Fan out one redacted Quick Open progress signal over the shared background-client-events bus.

        The frame is already `{scope_id, generation, revision, coverage}` -- the writer (`indexd`)
        redacted and coalesced it in `file_index.notify_search_progress`. This method only forwards it;
        it MUST NOT enrich the payload with anything (a role, a root, a session), because every field
        here is globally persisted, fanned out to all clients, and replayed on reconnect. Passing the
        frame through unchanged is what keeps the security boundary fail-closed at the transport."""
        return self.publish_background_client_event("search_progress", dict(frame), trigger="search-progress", cache="ready")

    def mark_search_progress_active(self) -> None:
        """Open/extend the window in which the client-event loop drains indexd's progress frames.

        The crawl runs in the `indexd` daemon, which cannot reach the shared client-events bus, so the
        web process that kicked it (`request_background_refresh` enqueue/promote) becomes the follower
        that drains the daemon's redacted frames and republishes them. Opening a bounded active window
        and waking the loop delivers the first frame within one poll instead of waiting for an unrelated
        deadline; an idle terminal never opens the window, so the daemon is never polled or kept hot."""
        self.search_progress_active_until = time.monotonic() + SEARCH_PROGRESS_ACTIVE_WINDOW_SECONDS
        record = self.client_watch_service.event_watcher_record
        record.next_search_progress_poll_at = 0.0
        record.wake_event.set()

    def drain_and_publish_search_progress(self) -> int:
        """Forward one batch of indexd's buffered progress frames onto the shared client-events bus.

        `notify_search_progress` builds the redacted `{scope_id, generation, revision, coverage}` frame
        inside the daemon but cannot publish it there (no App/broker). This FOLLOWER drains those frames
        and republishes each UNCHANGED through the one forwarder (`publish_search_progress`) -- the same
        path a same-process crawl would take -- so the palette receives the signal and pulls committed
        deltas by cursor. A frame that reports full coverage does not extend the active window; an
        unfinished one does, so draining tracks the crawl and stops after it settles."""
        frames = self.search_indexer.drain_search_progress()
        for frame in frames:
            coverage = frame.get("coverage") if isinstance(frame.get("coverage"), dict) else {}
            if not coverage.get("full_coverage"):
                self.search_progress_active_until = time.monotonic() + SEARCH_PROGRESS_ACTIVE_WINDOW_SECONDS
            self.publish_search_progress(frame)
        return len(frames)

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

    def keyed_client_event_patch(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        *,
        collection: str,
        record_key: Callable[[dict[str, Any]], str] | None = None,
        ignored_fields: frozenset[str] = frozenset(),
        always_fields: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        """Return one shared changed-record patch, or ``None`` for no meaningful change."""

        if not isinstance(previous, dict):
            return {"data": current}

        def indexed_records(payload: dict[str, Any]) -> dict[str, Any] | None:
            records = payload.get(collection)
            if isinstance(records, dict):
                return {str(key): value for key, value in records.items()}
            if not isinstance(records, list) or record_key is None:
                return None
            indexed: dict[str, Any] = {}
            for record in records:
                if not isinstance(record, dict):
                    return None
                key = str(record_key(record) or "")
                if not key or key in indexed:
                    return None
                indexed[key] = record
            return indexed

        previous_records = indexed_records(previous)
        current_records = indexed_records(current)
        if previous_records is None or current_records is None:
            return {"data": current}
        changes = {
            key: copy.deepcopy(record)
            for key, record in current_records.items()
            if self.stable_client_event_payload_signature(previous_records.get(key))
            != self.stable_client_event_payload_signature(record)
        }
        removed_keys = sorted(set(previous_records) - set(current_records))
        fields: dict[str, Any] = {}
        removed_fields: list[str] = []
        comparable_keys = (set(previous) | set(current)) - {collection} - CLIENT_EVENT_SIGNATURE_VOLATILE_KEYS - ignored_fields - always_fields
        for key in sorted(comparable_keys):
            if key not in current:
                removed_fields.append(key)
            elif key not in previous or self.stable_client_event_payload_signature(previous.get(key)) != self.stable_client_event_payload_signature(current.get(key)):
                fields[key] = copy.deepcopy(current[key])
        # An always-included field (the agent-window snapshot revision) is excluded from the change
        # comparison above so an unchanged roster never fans out a spurious patch. But when only the
        # revision advances -- the server re-measured the exact same rows under a new generation --
        # the browser still has to learn the new revision to clear its own stale marker, and it must
        # do so from this patch rather than an HTTP refetch. So an always-field value change is itself
        # a reason to emit an otherwise-minimal patch (empty changes, one field).
        always_field_changed = any(
            key in current
            and (
                key not in previous
                or self.stable_client_event_payload_signature(previous.get(key))
                != self.stable_client_event_payload_signature(current.get(key))
            )
            for key in always_fields
        )
        if not changes and not removed_keys and not fields and not removed_fields and not always_field_changed:
            return None
        for key in sorted(always_fields):
            if key in current:
                fields[key] = copy.deepcopy(current[key])
        return {
            "patch": True,
            "collection": collection,
            "changes": changes,
            "removed_keys": removed_keys,
            "fields": fields,
            "removed_fields": removed_fields,
        }

    def auto_approve_client_event_patch(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any] | None:
        return self.keyed_client_event_patch(
            previous,
            current,
            collection="sessions",
            ignored_fields=frozenset({"agent_window_snapshot_revision"}),
            always_fields=frozenset({"agent_window_snapshot_revision"}),
        )

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

    def server_attention_ack_event_poll_seconds(self) -> float:
        return self.jittered_interactive_event_poll_seconds(SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS)

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

    def tmux_signal_patch_payload(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
        patch = self.keyed_client_event_patch(
            previous,
            current,
            collection="windows",
            record_key=window_record_key,
            always_fields=frozenset({"generated_at", "compute_ms"}),
        )
        if patch is None:
            return {"data": current}
        removed_keys = list(patch.get("removed_keys") or [])
        if removed_keys and patch.get("patch") is True:
            fields = patch.get("fields") if isinstance(patch.get("fields"), dict) else {}
            fields["removed_window_keys"] = removed_keys
            removal_event = self.recent_tmux_signal_removal_event(current.get("generated_at"))
            if removal_event:
                fields["removed_window_event_at"] = removal_event.get("time")
                fields["removed_window_event_type"] = removal_event.get("type")
            patch["fields"] = fields
        return patch

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
        plan = ["git pull --ff-only origin main", "validate tracked xterm vendor assets", "python3 tools/static_build.py", "restart server"]
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
            build_error = cmd_error(static_build, "static build failed")[:360]
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

    def publish_update_notification_if_available(self) -> bool:
        status = self.update_status_payload(dryrun=False)
        target = status.get("target")
        if status.get("available") and status.get("notify") and target and target != self._update_last_target:
            self._update_last_target = target
            self.publish_client_event("update_available", status, trigger="update-check")
            return True
        return False

    def note_update_check(self, *, useful: bool, failed: bool = False, next_due_seconds: float = 0.0, enabled: bool = True) -> None:
        """Record one external update probe; disabled-idle sleeps are not probes."""
        now = time.time()
        with self.update_check_record.lock:
            record = self.update_check_record
            record.enabled = enabled
            record.next_due_at = now + max(0.0, next_due_seconds)
            if not enabled:
                return
            record.attempts += 1
            record.last_attempt_at = now
            if failed:
                record.failures += 1
            elif useful:
                record.useful += 1
                record.last_useful_at = now
            else:
                record.no_change += 1

    def update_check_recurring_work_snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.update_check_record.lock:
            record = self.update_check_record
            return {
                "owner": "update_check",
                "class": "external-reconcile",
                "cadence_seconds": max(0.0, record.next_due_at - record.last_attempt_at) if record.last_attempt_at > 0 else 0.0,
                "demanded": record.enabled,
                "attempts": record.attempts,
                "useful": record.useful,
                "no_change": record.no_change,
                "failures": record.failures,
                "last_attempt_at": record.last_attempt_at,
                "last_useful_at": record.last_useful_at,
                "next_due_in_seconds": max(0.0, record.next_due_at - now),
            }

    def update_check_loop(self) -> None:
        # Re-reads settings every iteration so the notification threshold takes effect without a
        # restart. When disabled, idles cheaply. Publishes update_available only when the available
        # target changes, so admins are nudged once per new version, not every interval.
        while True:
            section = self.updates_settings()
            if self.update_notify_level(section) == "none":
                self.note_update_check(useful=False, next_due_seconds=60.0, enabled=False)
                time.sleep(60)
                continue
            interval_minutes = section.get("check_interval_minutes", 60)
            try:
                interval = max(1.0, float(interval_minutes)) * 60.0
            except (TypeError, ValueError):
                interval = 3600.0
            try:
                useful = self.publish_update_notification_if_available()
                self.note_update_check(useful=useful, next_due_seconds=interval)
            except Exception as exc:
                logging.exception("update check failed: %s", exc)
                self.note_update_check(useful=False, failed=True, next_due_seconds=interval)
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

    def request_tabber_activity_refresh(self, trigger: str) -> bool:
        """Coalesce producer changes onto the owner-owned Tabber cache worker."""
        if not self.tabber_activity_has_recent_consumer():
            return False
        if not self.background_can_run(BACKGROUND_ROLE_TABBER_ACTIVITY):
            self.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "producer", "trigger": trigger})
            return False
        self.start_tabber_activity_cache_warmer()
        with self.activity_transcript_service.tabber_cache_lock:
            record = self.activity_transcript_service.tabber_warmer_record
            record.refresh_due_at = max(record.refresh_due_at, time.monotonic() + TABBER_ACTIVITY_REFRESH_DEBOUNCE_SECONDS)
            record.refresh_triggers.add(str(trigger))
            record.wake.set()
        return True

    def tabber_activity_has_recent_consumer(self) -> bool:
        now = time.monotonic()
        with self.activity_transcript_service.tabber_cache_lock:
            return self.activity_transcript_service.tabber_warmer_record.consumer_until > now

    def wake_client_event_watcher(self) -> None:
        return self._watch_bridge.wake_client_event_watcher(self)

    def note_client_event_recurring_work(self, record: ClientEventWatcherRecord, owner: str, *, useful: bool, failed: bool = False) -> None:
        return self._watch_bridge.note_client_event_recurring_work(self, record, owner, useful=useful, failed=failed)

    def client_event_recurring_work_snapshot(self, record: ClientEventWatcherRecord, now: float | None = None) -> list[dict[str, Any]]:
        return self._watch_bridge.client_event_recurring_work_snapshot(self, record, now)

    def client_event_recurring_work_demanded(self, owner: str) -> bool:
        return self._watch_bridge.client_event_recurring_work_demanded(self, owner)

    def client_event_watch_sleep_seconds(self, now: float, record: ClientEventWatcherRecord | None = None) -> float:
        return self._watch_bridge.client_event_watch_sleep_seconds(self, now, record)

    def update_client_watch_roots(self, roots: Any) -> dict[str, Any]:
        return self._watch_bridge.update_client_watch_roots(self, roots)

    def normalized_client_context_items(self, value: Any) -> list[dict[str, Any]]:
        return self._watch_bridge.normalized_client_context_items(self, value)

    def normalized_client_session_files(self, value: Any) -> list[dict[str, Any]]:
        return self._watch_bridge.normalized_client_session_files(self, value)

    def normalized_client_activity_summary(self, value: Any) -> dict[str, Any]:
        return composed_owner_for(self, "_watch_bridge", WatchBridge).normalized_client_activity_summary(self, value)

    def client_watch_roots_snapshot(self) -> list[str]:
        return self._watch_bridge.client_watch_roots_snapshot(self)

    def prune_client_watch_descriptors(self) -> None:
        return self._watch_bridge.prune_client_watch_descriptors(self)

    def touch_client_watch_descriptor(self, client_id: str) -> None:
        return self._watch_bridge.touch_client_watch_descriptor(self, client_id)

    def client_event_subscriber_disconnected(self, client_id: str) -> None:
        return self._watch_bridge.client_event_subscriber_disconnected(self, client_id)

    def client_watch_file_paths(self, *, background: bool) -> list[str]:
        return self._watch_bridge.client_watch_file_paths(self, background=background)

    def client_watch_files_snapshot(self) -> list[str]:
        return self._watch_bridge.client_watch_files_snapshot(self)

    def client_watch_background_files_snapshot(self) -> list[str]:
        return self._watch_bridge.client_watch_background_files_snapshot(self)

    def watchd_topology_signature(self) -> str | None:
        return self._watch_bridge.watchd_topology_signature(self)

    def watchd_transcript_paths(self) -> list[str]:
        return self._watch_bridge.watchd_transcript_paths(self)

    def watchd_descriptor_payloads(self) -> dict[str, dict[str, Any]]:
        return self._watch_bridge.watchd_descriptor_payloads(self)

    def apply_watchd_revision(self, record: ClientEventWatcherRecord, revision: dict[str, Any], *, reset: bool = False) -> list[str]:
        return self._watch_bridge.apply_watchd_revision(self, record, revision, reset=reset)

    def publish_watchd_recovery(self, record: ClientEventWatcherRecord) -> None:
        return self._watch_bridge.publish_watchd_recovery(self, record)

    def publish_watchd_failure(self, record: ClientEventWatcherRecord, response: dict[str, Any], *, action: str) -> None:
        return self._watch_bridge.publish_watchd_failure(self, record, response, action=action)

    @staticmethod
    def record_watchd_synced_generation(record: ClientEventWatcherRecord, response: dict[str, Any]) -> None:
        return WatchBridge.record_watchd_synced_generation(record, response)

    def sync_watchd_descriptors(self, record: ClientEventWatcherRecord) -> bool:
        return self._watch_bridge.sync_watchd_descriptors(self, record)

    def watchd_revision_loop(self, record: ClientEventWatcherRecord) -> None:
        return self._watch_bridge.watchd_revision_loop(self, record)

    def watchd_runtime_status(self) -> dict[str, Any]:
        return self._watch_bridge.watchd_runtime_status(self)

    def start_watchd_revision_watcher(self, record: ClientEventWatcherRecord) -> bool:
        return self._watch_bridge.start_watchd_revision_watcher(self, record)


    def record_filesystem_watch_snapshot(self, signature: tuple[Any, ...]) -> str:
        return self._watch_bridge.record_filesystem_watch_snapshot(self, signature)

    def filesystem_watch_record_for_token(self, token: str) -> dict[str, Any] | None:
        return self._watch_bridge.filesystem_watch_record_for_token(self, token)

    def latest_filesystem_watch_record(self) -> dict[str, Any] | None:
        return self._watch_bridge.latest_filesystem_watch_record(self)

    def filesystem_watch_signature_for_roots(
        self,
        roots: list[str],
    ) -> tuple[Any, ...]:
        return self._watch_bridge.filesystem_watch_signature_for_roots(self, roots)

    def filesystem_watch_full_plan(
        self,
        record: dict[str, Any],
        reason: str = "full",
    ) -> tuple[dict[str, Any], list[str]]:
        return self._watch_bridge.filesystem_watch_full_plan(self, record, reason)

    def filesystem_watch_diff_plan(
        self,
        since_token: str = "",
        force_full: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        return self._watch_bridge.filesystem_watch_diff_plan(self, since_token, force_full)

    @staticmethod
    def decode_filesystem_watch_batch_product(body: bytes) -> dict[str, Any]:
        return WatchBridge.decode_filesystem_watch_batch_product(body)

    def submit_filesystem_watch_batches(
        self,
        roots: list[str],
        identity_seed: str,
        *,
        delivery: str = "receipt",
    ) -> tuple[FilesystemWatchBatchProduct, ...]:
        return self._watch_bridge.submit_filesystem_watch_batches(self, roots, identity_seed, delivery=delivery)

    def submit_filesystem_watch_batch(
        self,
        roots: list[str],
        identity_seed: str,
        *,
        offset: int = 0,
        delivery: str = "receipt",
    ) -> FilesystemWatchBatchProduct:
        return self._watch_bridge.submit_filesystem_watch_batch(self, roots, identity_seed, offset=offset, delivery=delivery)

    def filesystem_watch_batch_identity_seed(
        self,
        base_payload: dict[str, Any],
        roots: list[str],
    ) -> str:
        return self._watch_bridge.filesystem_watch_batch_identity_seed(self, base_payload, roots)

    def cached_filesystem_watch_products(self, product_key: str) -> list[dict[str, Any]] | None:
        return self._watch_bridge.cached_filesystem_watch_products(self, product_key)

    def cache_filesystem_watch_products(
        self,
        products: list[dict[str, Any]],
        product_keys: set[str],
    ) -> None:
        return self._watch_bridge.cache_filesystem_watch_products(self, products, product_keys)

    def materialize_filesystem_watch_products(
        self,
        base_payload: dict[str, Any],
        roots: list[str],
        products: list[dict[str, Any]],
        *,
        product_keys: set[str],
    ) -> dict[str, Any]:
        return self._watch_bridge.materialize_filesystem_watch_products(self, base_payload, roots, products, product_keys=product_keys)

    def resolve_filesystem_watch_batches(
        self,
        batches: tuple[FilesystemWatchBatchProduct, ...],
        deadline_at: float,
        *,
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, Any]]:
        return self._watch_bridge.resolve_filesystem_watch_batches(self, batches, deadline_at, cancel_event=cancel_event)

    @staticmethod
    def filesystem_watch_payload_from_products(
        base_payload: dict[str, Any],
        roots: list[str],
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return WatchBridge.filesystem_watch_payload_from_products(base_payload, roots, products)

    def complete_filesystem_watch_diff_operation(
        self,
        receipt_fence: FilesystemWatchReceiptFence,
        request_id: str,
        base_payload: dict[str, Any],
        roots: list[str],
        deadline_at: float,
        identity_seed: str,
    ) -> None:
        return self._watch_bridge.complete_filesystem_watch_diff_operation(self, receipt_fence, request_id, base_payload, roots, deadline_at, identity_seed)

    def accept_filesystem_watch_diff_operation(
        self,
        request_id: str,
        base_payload: dict[str, Any],
        roots: list[str],
        identity_seed: str,
        reservation: JobdOperationReservation,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._watch_bridge.accept_filesystem_watch_diff_operation(self, request_id, base_payload, roots, identity_seed, reservation)

    def filesystem_watch_diff_http_payload(
        self,
        since_token: str = "",
        force_full: bool = False,
        request_id: str = "",
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._watch_bridge.filesystem_watch_diff_http_payload(self, since_token, force_full, request_id)

    def clear_transcript_content_caches(self) -> None:
        return composed_owner_for(self, "_watch_bridge", WatchBridge).clear_transcript_content_caches(self)

    def clear_transcript_caches(self) -> None:
        return composed_owner_for(self, "_watch_bridge", WatchBridge).clear_transcript_caches(self)

    def start_client_watch_snapshot_publish(self) -> bool:
        return self._watch_bridge.start_client_watch_snapshot_publish(self)

    def client_watch_snapshot_is_current(self, record: ClientEventWatcherRecord, worker: threading.Thread) -> bool:
        return self._watch_bridge.client_watch_snapshot_is_current(self, record, worker)

    def publish_client_watch_snapshot(
        self,
        record: ClientEventWatcherRecord | None = None,
        generation: int | None = None,
    ) -> None:
        return self._watch_bridge.publish_client_watch_snapshot(self, record, generation)


    def record_dependency_invalidation(self, trigger: str) -> None:
        return self._watch_bridge.record_dependency_invalidation(self, trigger)

    def publish_context_items_ready_events(self, trigger: str = "watch") -> list[str]:
        return self._watch_bridge.publish_context_items_ready_events(self, trigger)

    def publish_activity_summary_ready_events(self, trigger: str = "watch") -> list[str]:
        return self._watch_bridge.publish_activity_summary_ready_events(self, trigger)

    def publish_session_files_ready_events(self, trigger: str = "watch", *, force: bool = False) -> list[str]:
        return self._watch_bridge.publish_session_files_ready_events(self, trigger, force=force)

    def start_status_generation_watcher(self, record: ClientEventWatcherRecord) -> bool:
        return self._watch_bridge.start_status_generation_watcher(self, record)

    def stop_status_generation_watcher(self, record: ClientEventWatcherRecord) -> None:
        return self._watch_bridge.stop_status_generation_watcher(self, record)

    def status_generation_wait_loop(self, record: ClientEventWatcherRecord) -> None:
        return self._watch_bridge.status_generation_wait_loop(self, record)

    def poll_tmux_signals_client_event_once(self) -> list[str]:
        return self._watch_bridge.poll_tmux_signals_client_event_once(self)

    def handle_tmux_signal_event(self, event: dict[str, Any]) -> None:
        return self._watch_bridge.handle_tmux_signal_event(self, event)

    def tmux_signal_event_watcher_healthy(self) -> bool:
        return self._watch_bridge.tmux_signal_event_watcher_healthy(self)

    def tmux_signal_event_watcher_status(self) -> dict[str, Any]:
        return self._watch_bridge.tmux_signal_event_watcher_status(self)

    def log_tmux_signal_event_error(self, message: str) -> None:
        return self._watch_bridge.log_tmux_signal_event_error(self, message)

    def start_tmux_signal_event_watcher(self) -> bool:
        return self._watch_bridge.start_tmux_signal_event_watcher(self)

    def stop_tmux_signal_event_watcher(self) -> None:
        return self._watch_bridge.stop_tmux_signal_event_watcher(self)

    def poll_watched_prs_client_event_once(self) -> list[str]:
        return self._watch_bridge.poll_watched_prs_client_event_once(self)

    def start_client_event_watcher(self) -> None:
        return self._watch_bridge.start_client_event_watcher(self)

    def stop_client_event_watcher(self) -> None:
        return self._watch_bridge.stop_client_event_watcher(self)

    def stop_client_event_watcher_if_idle(self) -> bool:
        return self._watch_bridge.stop_client_event_watcher_if_idle(self)


    def client_event_watch_loop(self, record: ClientEventWatcherRecord | None = None) -> None:
        return self._watch_bridge.client_event_watch_loop(self, record)

    def cache_set_limited(self, cache: dict[Any, Any], key: Any, value: Any, limit: int) -> None:
        return self._session_files_coordinator.cache_set_limited(self, cache, key, value, limit)

    def session_files_exclusion_policy(self) -> exclusions.ExclusionPolicy:
        return self._session_files_coordinator.session_files_exclusion_policy(self)

    def session_files_cache_key( self, kind: str, infos: dict[str, SessionInfo], session: str | None, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> tuple[Any, ...]:
        return self._session_files_coordinator.session_files_cache_key(self, kind, infos, session, hours, from_ref, to_ref, repo_refs)

    def session_files_refresh_request_payload( self, cache_key: tuple[Any, ...], session: str | None, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> dict[str, Any]:
        return self._session_files_coordinator.session_files_refresh_request_payload(self, cache_key, session, hours, from_ref, to_ref, repo_refs)

    def requested_session_files_cache_key( self, payload: dict[str, Any], fallback: tuple[Any, ...], ) -> tuple[Any, ...]:
        return self._session_files_coordinator.requested_session_files_cache_key(self, payload, fallback)

    # ---- Repository-state record (DOIT.optimize-backends) ------------------
    # One dirty generation per canonical Git root, bumped by the native
    # filesystem watcher (worktree AND .git metadata events). A cached identity
    # is reusable only while the watcher is healthy, the repo lies under a
    # watched root, the generation is unchanged, and the entry is younger than
    # the safety-reconciliation bound (missed/coalesced events self-heal).
    SESSION_FILES_GIT_IDENTITY_SAFETY_SECONDS = 60.0

    def repo_dirty_generation(self, repo_text: str) -> int:
        return self._session_files_coordinator.repo_dirty_generation(self, repo_text)

    def mark_repo_state_dirty(self, changed_paths: list[Path]) -> None:
        return self._session_files_coordinator.mark_repo_state_dirty(self, changed_paths)

    def store_git_identity(self, identity_key: tuple[Any, ...], dirty_generation: int, identity: tuple[Any, ...]) -> None:
        return self._session_files_coordinator.store_git_identity(self, identity_key, dirty_generation, identity)

    def watcher_covers_repo(self, repo: Path) -> bool:
        return self._session_files_coordinator.watcher_covers_repo(self, repo)

    def reusable_git_identity(self, identity_key: tuple[Any, ...], repo: Path) -> tuple[Any, ...] | None:
        return self._session_files_coordinator.reusable_git_identity(self, identity_key, repo)

    def session_files_disk_cache_path(self, key: tuple[Any, ...]) -> tuple[Path, str]: # Stable logical view identity only (kind, version, session, hours, refs, # per-repo ref overrides): the volatile info/repo signatures (key[-2:]) # are a replaceable source generation stored INSIDE the record, so agent # status or transcript appends REPLACE one durable file per view instead # of minting a new filename per generation.
        return self._session_files_coordinator.session_files_disk_cache_path(self, key)

    def session_files_source_generation(self, key: tuple[Any, ...]) -> str:
        return self._session_files_coordinator.session_files_source_generation(self, key)

    def session_files_disk_manifest_path(self, signature: str) -> Path:
        return self._session_files_coordinator.session_files_disk_manifest_path(self, signature)

    def prune_session_files_disk_cache( self, *, max_age_seconds: float | None = None, max_bytes: int | None = None, now: float | None = None, ) -> dict[str, Any]:
        return self._session_files_coordinator.prune_session_files_disk_cache(self, max_age_seconds=max_age_seconds, max_bytes=max_bytes, now=now)

    def run_session_files_disk_cache_prune(self, record: SessionFilesDiskPruneRecord | None = None) -> None:
        return self._session_files_coordinator.run_session_files_disk_cache_prune(self, record)

    def request_session_files_disk_cache_prune(self, reason: str = "") -> bool:
        return self._session_files_coordinator.request_session_files_disk_cache_prune(self, reason)

    def session_files_payload_signature(self, payload: SessionFilesPayload | dict[str, Any]) -> str:
        return self._session_files_coordinator.session_files_payload_signature(self, payload)

    def set_session_files_memory_cache( self, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus, stored_at: float | None = None, ) -> None:
        return self._session_files_coordinator.set_session_files_memory_cache(self, key, payload, status, stored_at)

    def read_session_files_disk_cache( self, key: tuple[Any, ...], max_age_seconds: float | None = None, allow_stale: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
        return self._session_files_coordinator.read_session_files_disk_cache(self, key, max_age_seconds, allow_stale)

    def session_files_published_cache(self):
        return self._session_files_coordinator.session_files_published_cache(self)

    def write_session_files_disk_cache_unlocked( self, path: Path, signature: str, payload: SessionFilesPayload, status: HTTPStatus, source_generation: str = "", ) -> None:
        return self._session_files_coordinator.write_session_files_disk_cache_unlocked(self, path, signature, payload, status, source_generation)

    def write_session_files_disk_cache(self, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
        return self._session_files_coordinator.write_session_files_disk_cache(self, key, payload, status)

    def record_session_files_phase(self, phase: str, compute_ms: float, details: dict[str, Any]) -> None:
        return self._session_files_coordinator.record_session_files_phase(self, phase, compute_ms, details)

    def shared_git_identity(self, repo: Path, from_ref: str | None, to_ref: str | None) -> tuple[tuple[Any, ...], str]:
        return self._session_files_coordinator.shared_git_identity(self, repo, from_ref, to_ref)

    def shared_session_files_git_snapshot( self, repo: Path, from_ref: str | None, to_ref: str | None, *, identity: tuple[Any, ...] | None = None, ) -> dict[str, Any]:
        return self._session_files_coordinator.shared_session_files_git_snapshot(self, repo, from_ref, to_ref, identity=identity)

    def complete_session_files_work( self, key: tuple[Any, ...], record: SessionFilesWorkRecord, result: tuple[SessionFilesPayload, HTTPStatus, bool, float] | None = None, error: Exception | None = None, ) -> None:
        return self._session_files_coordinator.complete_session_files_work(self, key, record, result, error)

    def compute_session_files_cache_entry( self, key: tuple[Any, ...], compute: Callable[[], tuple[SessionFilesPayload, HTTPStatus]], *, reserved: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float]:
        return self._session_files_coordinator.compute_session_files_cache_entry(self, key, compute, reserved=reserved)

    def get_session_files_cache( self, key: tuple[Any, ...], max_age_seconds: float | None = None, allow_stale: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus, bool, float] | None:
        return self._session_files_coordinator.get_session_files_cache(self, key, max_age_seconds, allow_stale)

    def set_session_files_cache(self, key: tuple[Any, ...], payload: SessionFilesPayload, status: HTTPStatus) -> None:
        return self._session_files_coordinator.set_session_files_cache(self, key, payload, status)

    def clear_session_files_cache(self) -> None:
        return self._session_files_coordinator.clear_session_files_cache(self)

    def session_files_git_identity_for_cache_key(self, cache_key: tuple[Any, ...] | None, repo: Path) -> tuple[Any, ...] | None:
        return self._session_files_coordinator.session_files_git_identity_for_cache_key(self, cache_key, repo)

    def session_files_git_snapshot_provider(self, cache_key: tuple[Any, ...] | None) -> Callable[[Path, str | None, str | None], dict[str, Any]]:
        return self._session_files_coordinator.session_files_git_snapshot_provider(self, cache_key)

    def compute_session_files_payload_for_info( self, info: SessionInfo, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...] | None = None, ) -> SessionFilesPayload:
        return self._session_files_coordinator.compute_session_files_payload_for_info(self, info, hours, from_ref, to_ref, repo_refs, cache_key)

    def session_files_view_coalesce_identity(self, cache_key: tuple[Any, ...]) -> tuple[str, int]:
        return self._session_files_coordinator.session_files_view_coalesce_identity(self, cache_key)

    def session_files_jobd_source_profile(self, cache_key: tuple[Any, ...], requester: str) -> dict[str, str | int]:
        return self._session_files_coordinator.session_files_jobd_source_profile(self, cache_key, requester)

    @staticmethod
    def session_files_jobd_repository_states(cache_key: tuple[Any, ...]) -> list[dict[str, object]]:
        return SessionFilesCoordinator.session_files_jobd_repository_states(cache_key)

    def submit_session_files_job( self, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...], *, priority: str = "freshness", requester: str = "unknown", ) -> tuple[dict[str, Any], str, int]:
        return self._session_files_coordinator.submit_session_files_job(self, session, infos, hours, from_ref, to_ref, repo_refs, cache_key, priority=priority, requester=requester)

    def compute_session_files_payload_via_jobd( self, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...], *, priority: str = "freshness", requester: str = "unknown", ) -> tuple[SessionFilesPayload, HTTPStatus]:
        return self._session_files_coordinator.compute_session_files_payload_via_jobd(self, session, infos, hours, from_ref, to_ref, repo_refs, cache_key, priority=priority, requester=requester)

    def session_files_payload_from_product(self, body: bytes) -> tuple[SessionFilesPayload, HTTPStatus]:
        return self._session_files_coordinator.session_files_payload_from_product(self, body)

    def session_files_payload_from_job(self, job: dict[str, Any]) -> tuple[SessionFilesPayload, HTTPStatus]:
        return self._session_files_coordinator.session_files_payload_from_job(self, job)

    def wait_for_session_files_operation_job( self, job_id: str, deadline_at: float, ) -> tuple[SessionFilesPayload, HTTPStatus]:
        return self._session_files_coordinator.wait_for_session_files_operation_job(self, job_id, deadline_at)

    def complete_session_files_operation( self, operation_id: str, request_id: str, job_id: str, cache_key: tuple[Any, ...], deadline_at: float, ) -> None:
        return self._session_files_coordinator.complete_session_files_operation(self, operation_id, request_id, job_id, cache_key, deadline_at)

    def start_session_files_operation( self, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, cache_key: tuple[Any, ...], *, priority: str, requester: str, ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._session_files_coordinator.start_session_files_operation(self, session, infos, hours, from_ref, to_ref, repo_refs, cache_key, priority=priority, requester=requester)

    def refresh_session_files_payload_cache( self, cache_key: tuple[Any, ...], session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> None:
        return self._session_files_coordinator.refresh_session_files_payload_cache(self, cache_key, session, infos, hours, from_ref, to_ref, repo_refs)

    def refresh_session_files_info_cache( self, cache_key: tuple[Any, ...], info: SessionInfo, hours: float, from_ref: str | None, to_ref: str | None, repo_refs: dict[str, dict[str, str]] | None, ) -> None:
        return self._session_files_coordinator.refresh_session_files_info_cache(self, cache_key, info, hours, from_ref, to_ref, repo_refs)

    def start_session_files_cache_refresh(self, cache_key: tuple[Any, ...], target: Any, *args: Any) -> bool:
        return self._session_files_coordinator.start_session_files_cache_refresh(self, cache_key, target, *args)

    def start_requested_session_files_cache_refresh(self, payload: dict[str, Any]) -> bool:
        return self._session_files_coordinator.start_requested_session_files_cache_refresh(self, payload)

    def cached_session_files_payload_for_info( self, info: SessionInfo, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, *, wait_for_fresh: bool = True, ) -> SessionFilesPayload:
        return self._session_files_coordinator.cached_session_files_payload_for_info(self, info, hours, from_ref, to_ref, repo_refs, wait_for_fresh=wait_for_fresh)

    def warm_start_session_files_payload_cache(self) -> None:
        return self._session_files_coordinator.warm_start_session_files_payload_cache(self)

    def warm_start_tabber_activity_cache(self) -> None:
        return self._session_files_coordinator.warm_start_tabber_activity_cache(self)

    def cached_session_files_payloads_for_infos( self, infos: dict[str, SessionInfo], hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, ) -> dict[str, SessionFilesPayload]:
        return self._session_files_coordinator.cached_session_files_payloads_for_infos(self, infos, hours, from_ref, to_ref, repo_refs)

    def session_files_payload_for_infos( self, session: str | None, infos: dict[str, SessionInfo], hours: float, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, requester: str = "api-session-files", extra_errors: list[str | dict[str, Any]] | None = None, accepted_operation: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus]:
        return self._session_files_coordinator.session_files_payload_for_infos(self, session, infos, hours, from_ref, to_ref, repo_refs, force, requester, extra_errors, accepted_operation)

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

    def cached_transcripts_work_graph(self, session: str) -> dict[str, Any] | None:
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            payload = self.activity_transcript_service.transcripts_payload_cache_record.payload
            sessions = payload.get("sessions") if isinstance(payload, dict) else None
            cached_session = sessions.get(session) if isinstance(sessions, dict) else None
            graph = cached_session.get("work_graph") if isinstance(cached_session, dict) else None
            return copy.deepcopy(graph) if isinstance(graph, dict) else None

    def begin_transcripts_payload_work(
        self,
        worker: object | None,
        *,
        replace: bool = False,
        queue_rebuild_after: float | None = None,
        queue_rebuild_publish: bool = False,
        pending_generation_out: list[int] | None = None,
    ) -> int:
        """Claim the single-flight build guard, or queue one follow-up build for a caller it cannot answer.

        ``queue_rebuild_after`` is a ``time.monotonic()`` reading the caller must be answered at or
        after. Deciding that here, under the same lock that refuses the guard, is what makes it
        impossible for the in-flight worker to finish in the gap and leave the caller with neither a
        build of its own nor a queued one.

        ``pending_generation_out`` receives, under that same lock, the generation of the build that
        will answer this caller -- the one claimed here, the in-flight one that already observes the
        request, or the queued follow-up. It is an out-parameter rather than a second read of the
        record because ANY second read is the gap: the in-flight build can commit and release the
        guard between the two acquisitions, after which the record no longer remembers which build
        the caller was promised and the caller is told no build was accepted.
        """

        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if record.worker is not None and not replace:
                started_at = record.worker_started_at
                # A worker still within the deadline holds the single-flight guard.
                # Past it, the worker is treated as stalled and superseded so a hung
                # build cannot refuse every future refresh; the stale worker's later
                # commit/finish is a no-op because the generation has advanced.
                if started_at is None or time.monotonic() - started_at < TRANSCRIPTS_PAYLOAD_WORKER_DEADLINE_SECONDS:
                    if queue_rebuild_after is not None and (started_at is None or started_at < queue_rebuild_after):
                        record.rebuild_requested = True
                        record.rebuild_publish = record.rebuild_publish or queue_rebuild_publish
                        # The queued follow-up commits the generation after the in-flight one.
                        pending_generation = record.generation + 1
                    else:
                        # The in-flight build began at or after the request, so it already observes
                        # what this caller is asking about.
                        pending_generation = record.generation
                    if pending_generation_out is not None:
                        pending_generation_out.append(pending_generation)
                    return 0
            record.generation += 1
            record.worker = worker
            record.worker_started_at = time.monotonic()
            record.publish_requested = False
            if pending_generation_out is not None:
                pending_generation_out.append(record.generation)
            return record.generation

    def commit_transcripts_payload_cache(self, payload: dict[str, Any], generation: int) -> bool:
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if generation <= 0 or record.generation != generation:
                return False
            # Stamp the committing identity into the payload itself, not beside it. Every consumer
            # -- the HTTP cache hit, the client-events push, and a direct build -- carries the same
            # identity, so a browser can tell whether the model it rendered came from a build that
            # observed the state it asked about, instead of inferring it from arrival order.
            self.stamp_metadata_identity(payload, generation)
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
            record.release_worker()
        # Releasing the single-flight guard is the only moment a queued follow-up build can run, and
        # every build path ends here, so this is the one owner rather than a copy per call site.
        self.start_queued_transcripts_payload_rebuild()
        return True

    def start_queued_transcripts_payload_rebuild(self) -> bool:
        """Run the rebuild a forced refresh could not start because an older build held the guard.

        A forced refresh must observe state that exists when it is issued. The single-flight guard
        used to hand it the result of a build that began earlier, so a session created after that
        build started could never appear in the payload the force returned or published, and nothing
        else re-ran: the browser stayed on pre-create metadata until some unrelated event fired.
        The request is coalesced FORWARD onto exactly one follow-up build rather than backward onto
        an older one, so repeated forces cost at most one extra build, never a rebuild storm.
        """

        with self.activity_transcript_service.transcripts_payload_cache_lock:
            record = self.activity_transcript_service.transcripts_payload_cache_record
            if not record.rebuild_requested or record.worker is not None:
                return False
            publish = record.rebuild_publish
            record.rebuild_requested = False
            record.rebuild_publish = False
        return self.start_transcripts_payload_refresh(publish=publish)

    def set_transcripts_payload_cache(self, payload: dict[str, Any]) -> None:
        generation = self.begin_transcripts_payload_work(None, replace=True)
        self.commit_transcripts_payload_cache(payload, generation)

    def start_transcripts_payload_refresh(
        self,
        publish: bool = False,
        defer: bool = False,
        *,
        not_before: float | None = None,
        pending_generation_out: list[int] | None = None,
    ) -> bool:
        """Start a metadata rebuild.

        ``not_before`` is a ``time.monotonic()`` reading the caller must be answered at or after. An
        in-flight build that began earlier cannot see what the caller is asking about, so instead of
        silently adopting it this queues exactly one follow-up build that starts once it finishes.

        ``pending_generation_out`` is passed straight through to the guard, so a caller that needs
        the identity of the build answering it reads that identity from the one lock acquisition
        that decided it.
        """

        generation = 0
        worker: object | None = None
        def run() -> None:
            self.refresh_transcripts_payload_cache(publish, generation=generation, worker=worker)

        if defer:
            worker = threading.Timer(0.05, run)
            worker.daemon = True
        else:
            worker = threading.Thread(target=run, daemon=True)
        generation = self.begin_transcripts_payload_work(
            worker,
            queue_rebuild_after=not_before,
            queue_rebuild_publish=publish,
            pending_generation_out=pending_generation_out,
        )
        if generation <= 0:
            if publish:
                with self.activity_transcript_service.transcripts_payload_cache_lock:
                    record = self.activity_transcript_service.transcripts_payload_cache_record
                    if record.worker is not None:
                        record.publish_requested = True
            return False
        if publish:
            with self.activity_transcript_service.transcripts_payload_cache_lock:
                record = self.activity_transcript_service.transcripts_payload_cache_record
                if record.generation == generation and record.worker is worker:
                    record.publish_requested = True
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
            with self.activity_transcript_service.transcripts_payload_cache_lock:
                record = self.activity_transcript_service.transcripts_payload_cache_record
                should_publish = publish or (
                    record.generation == generation
                    and record.worker is current_worker
                    and record.publish_requested
                )
            if should_publish:
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

    def watched_prs_payload(self, allow_network: bool = True) -> dict[str, Any]: # resolve the github.watched_prs watchlist to live PR metadata, independent of any open # session's branch. The server-side SSE loop refreshes it on a fixed slow cadence so a big watchlist # does not exhaust the GitHub rate limit.
        return self._activity_cache.watched_prs_payload(self, allow_network)

    def tabber_activity_agents_snapshot(self, force: bool = False) -> list[dict[str, Any]]:
        return self._activity_cache.tabber_activity_agents_snapshot(self, force)

    def activity_session_info_payload( self, session: str, info: SessionInfo, work: dict[str, Any], files_payload: dict[str, Any], summary: dict[str, Any], recent_events: list[dict[str, Any]] | None = None, locale: str = "en", ) -> dict[str, Any]:
        return self._activity_cache.activity_session_info_payload(self, session, info, work, files_payload, summary, recent_events, locale)

    def cached_activity_work_by_session(self) -> dict[str, dict[str, Any]]:
        return self._activity_cache.cached_activity_work_by_session(self)

    def restore_activity_summary_web_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._activity_cache.restore_activity_summary_web_state(self, payload)

    def activity_summary_payload(self, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        return self._activity_cache.activity_summary_payload(self, force, locale, session_scope, hours)

    def activity_summary_bytes(self, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0) -> tuple[bytes, HTTPStatus]:
        return composed_owner_for(self, "_activity_cache", ActivityCache).activity_summary_bytes(self, force, locale, session_scope, hours)

    def assemble_activity_summary_payload( self, force: bool = False, locale: str = "en", session_scope: Any = "configured", hours: Any = 24.0, work_by_session: dict[str, dict[str, Any]] | None = None, timings: dict[str, float] | None = None, ) -> dict[str, Any]:
        return self._activity_cache.assemble_activity_summary_payload(self, force, locale, session_scope, hours, work_by_session, timings)

    def _activity_summary_payload_owner( self, *, force: bool, locale: str, session_names: list[str], scope: str, bounded_hours: float, sessions: dict[str, SessionInfo], errors: list[str], work_by_session: dict[str, dict[str, Any]], timings: dict[str, float] | None, ) -> dict[str, Any]:
        return self._activity_cache._activity_summary_payload_owner(self, force=force, locale=locale, session_names=session_names, scope=scope, bounded_hours=bounded_hours, sessions=sessions, errors=errors, work_by_session=work_by_session, timings=timings)

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
        # Re-lease/enqueue promptly when indexed-root settings change: added roots start their
        # layer-1 crawl and removed roots release the scheduler obligation, without waiting for the
        # asynchronous watchd settings revision. Guarded to the background owner inside.
        if isinstance(patch, dict) and isinstance(patch.get("file_explorer"), dict) and "indexed_dirs" in patch["file_explorer"]:
            self.refresh_search_indexer_schedule()
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
        saved = self.event_log.append(
            session,
            event_type,
            message,
            details,
            message_key=message_key,
            message_params=message_params,
        )
        # The append is durable before this small invalidation is published.  A
        # follower can therefore refetch the same log file immediately; the
        # existing background manifest/control fan-out covers its SSE clients.
        self.publish_background_client_event(
            "event_log_changed",
            {"session": str(session or "")},
            trigger="event-log",
            cache="ready",
        )
        return saved

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
            item_details = item.get("details") if isinstance(item.get("details"), dict) else {}
            if item_details.get("measurement_scope") == "capture":
                self.performance_capture_record_count_total += 1
                item_details["capture_sequence"] = self.performance_capture_record_count_total
                self.performance_capture_records.append(item)
        return item

    def performance_metrics_payload(self, window_seconds: float = PERFORMANCE_SUMMARY_WINDOW_SECONDS, measurement_scope: str = "") -> dict[str, Any]:
        now = time.time()
        cutoff = now - max(1.0, float(window_seconds or PERFORMANCE_SUMMARY_WINDOW_SECONDS))
        requested_scope = str(measurement_scope or "").strip()
        with self.performance_record_lock:
            records = [dict(item) for item in self.performance_records]
            scoped_records = [dict(item) for item in self.performance_capture_records] if requested_scope == "capture" else records
            capture_total = self.performance_capture_record_count_total
        # Capture rows have their own bounded ring and unique request digests. Do not apply the
        # diagnostics UI's 60-second summary window to a 200-request measurement run: a slow but
        # valid run must remain joinable, and the caller selects its exact rows by digest.
        window_records = scoped_records if requested_scope == "capture" else [
            item for item in scoped_records if self.float_value(item.get("time"), 0.0) >= cutoff
        ]
        if requested_scope:
            window_records = [
                item for item in window_records
                if isinstance(item.get("details"), dict) and item["details"].get("measurement_scope") == requested_scope
            ]
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
                "request_total_ms_total": 0.0,
                "request_total_ms_max": 0.0,
                "accept_to_route_ms_total": 0.0,
                "accept_to_route_ms_max": 0.0,
            })
            summary["count"] += 1
            compute_ms = max(0.0, self.float_value(item.get("compute_ms"), 0.0))
            summary["compute_ms_total"] += compute_ms
            summary["compute_ms_max"] = max(summary["compute_ms_max"], compute_ms)
            summary["payload_bytes_total"] += max(0, int(self.float_value(item.get("payload_bytes"), 0.0)))
            cache_status = str(item.get("cache_status") or "")
            if cache_status:
                summary["cache"][cache_status] = int(summary["cache"].get(cache_status, 0)) + 1
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            for field in ("request_total_ms", "accept_to_route_ms"):
                value = max(0.0, self.float_value(details.get(field), 0.0))
                summary[f"{field}_total"] += value
                summary[f"{field}_max"] = max(summary[f"{field}_max"], value)
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
                "request_total_ms_avg": round(float(item["request_total_ms_total"]) / count, 3),
                "request_total_ms_max": round(float(item["request_total_ms_max"]), 3),
                "accept_to_route_ms_avg": round(float(item["accept_to_route_ms_total"]) / count, 3),
                "accept_to_route_ms_max": round(float(item["accept_to_route_ms_max"]), 3),
                "payload_bytes_total": item["payload_bytes_total"],
                "cache": item["cache"],
            })
        summary_rows.sort(key=lambda item: (-float(item["compute_ms_max"]), item["role"], item["surface"]))
        top_payload_rows = sorted(
            summary_rows,
            key=lambda item: (-int(item["payload_bytes_total"]), -int(item["count"]), item["role"], item["surface"]),
        )
        payload = {
            "window_seconds": max(1.0, float(window_seconds or PERFORMANCE_SUMMARY_WINDOW_SECONDS)),
            "record_limit": PERFORMANCE_RECORD_LIMIT,
            "record_count": len(records),
            "summary": summary_rows,
            "top_payload_bytes": top_payload_rows,
            # A scoped request must return its OWN recent rows, not the global ring tail (which
            # unrelated churn evicts): `window_records` is already scope+window filtered (W9).
            "recent": window_records if requested_scope == "capture" else records[-PERFORMANCE_RECENT_LIMIT:],
        }
        if requested_scope == "capture":
            sequences = [
                int(item["details"].get("capture_sequence") or 0)
                for item in window_records
                if isinstance(item.get("details"), dict)
            ]
            payload["capture"] = {
                "capacity": PERFORMANCE_CAPTURE_RECORD_LIMIT,
                "retained": len(window_records),
                "total": capture_total,
                "evicted": max(0, capture_total - len(window_records)),
                "first_sequence": min(sequences, default=0),
                "last_sequence": max(sequences, default=0),
            }
        return payload

    def server_cpu_budget_top_consumers(
        self,
        limit: int = 3,
        window_seconds: float = SERVER_CPU_BUDGET_SUSTAINED_SECONDS,
    ) -> list[dict[str, Any]]:
        """Return bounded endpoint/background owners ranked by aggregate compute.

        The window must be the breach window the caller is explaining. This defaulted to
        PERFORMANCE_SUMMARY_WINDOW_SECONDS (60s) while the warning it feeds described a 300s
        breach, so the totals under-counted the period they were presented as covering by 5x.
        """

        rows = [
            dict(row)
            for row in self.performance_metrics_payload(window_seconds=window_seconds).get("summary", [])
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
        record = self.stats_collection_state.cpu_budget_record
        # An ABSENT sample is not a 0% sample. Reading it as 0.0 would have cleared
        # `exceeded_since`/`warning_emitted`/`top_consumers` below -- silently cancelling a
        # breach in progress because nobody measured, which is the same fabricated zero as
        # the panel's, with state-machine consequences.
        if sample.get("cpu_percent") is None:
            return self.server_cpu_budget_payload()
        cpu_percent = max(0.0, self.float_value(sample.get("cpu_percent"), 0.0))
        record.current_percent = cpu_percent
        previous_sample_at = record.last_sample_at
        record.last_sample_at = sample_time
        if cpu_percent <= SERVER_CPU_BUDGET_PERCENT:
            record.exceeded_since = 0.0
            record.warning_emitted = False
            record.top_consumers = []
            record.cpu_ms_since_exceeded = 0.0
            return self.server_cpu_budget_payload(now=sample_time, advancing=True)
        if record.exceeded_since <= 0:
            record.exceeded_since = sample_time
            record.cpu_ms_since_exceeded = 0.0
        elif previous_sample_at > 0:
            # Integrate this sample's CPU over the gap it represents. Every sample since
            # exceeded_since was itself a breach, so this is the CPU the warning is about.
            record.cpu_ms_since_exceeded += cpu_percent / 100.0 * max(0.0, sample_time - previous_sample_at) * 1000.0
        sustained_seconds = max(0.0, sample_time - record.exceeded_since)
        if sustained_seconds >= SERVER_CPU_BUDGET_SUSTAINED_SECONDS and not record.warning_emitted:
            record.warning_emitted = True
            record.last_warning_at = sample_time
            record.top_consumers = self.server_cpu_budget_top_consumers(window_seconds=sustained_seconds)
            attributed_ms = sum(float(row["compute_ms_total"]) for row in record.top_consumers)
            consumed_ms = max(0.0, record.cpu_ms_since_exceeded)
            attributed_percent = (attributed_ms / consumed_ms * 100.0) if consumed_ms > 0 else 0.0
            consumer_text = ", ".join(
                f"{row['role']}:{row['surface']}={row['compute_ms_total']:.1f}ms"
                for row in record.top_consumers
            ) or "no profiled consumers"
            # State the coverage. Only code that calls record_performance_sample appears in the
            # consumer list, so a background thread burning the CPU is invisible to it and the
            # top profiled endpoint gets read as the cause. A live 7771 breach attributed 0.9ms
            # of ~267 CPU-seconds and named an HTTP endpoint worth 0.7% of the process.
            unattributed_percent = max(0.0, 100.0 - attributed_percent)
            verdict = (
                "the cause is unprofiled, not the list below"
                if attributed_percent < SERVER_CPU_BUDGET_ATTRIBUTION_MIN_PERCENT
                else "the list below covers most of it"
            )
            message = (
                f"YOLOmux CPU {cpu_percent:.1f}% (latest 1s sample) exceeded {SERVER_CPU_BUDGET_PERCENT:.0f}% "
                f"for {sustained_seconds:.0f}s, consuming {consumed_ms / 1000.0:.1f} CPU-s; "
                f"profiling attributes {attributed_percent:.1f}%, unattributed {unattributed_percent:.1f}% "
                f"— {verdict}; top profiled compute: {consumer_text}"
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
                    "cpu_ms_consumed": round(consumed_ms, 3),
                    "attributed_ms": round(attributed_ms, 3),
                    "attributed_percent": round(attributed_percent, 3),
                    "top_consumers": json.dumps(record.top_consumers, separators=(",", ":")),
                },
            )
        return self.server_cpu_budget_payload(now=sample_time, advancing=True)

    def server_cpu_budget_payload(
        self,
        *,
        now: float | None = None,
        advancing: bool = False,
        sample: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish the CPU budget.

        `now` is only a CLOCK and `advancing` is only "I just advanced the record with a fresh
        sample, so do not call it stale". They used to be one parameter, which meant a caller
        could not supply a consistent read timestamp without also suppressing the staleness
        test -- exactly what a single-snapshot response needs to do.

        `sample` lets a caller hand in the reading it has already taken, so one response cannot
        read the cache twice and publish two different answers about the same sample's age.
        """

        record = self.stats_collection_state.cpu_budget_record
        read_at = float(now if now is not None else time.time())
        sustained_seconds = max(0.0, read_at - record.exceeded_since) if record.exceeded_since > 0 else 0.0
        if sample is None:
            with self.stats_collection_state.sample_lock:
                sample = dict(self.stats_collection_state.sample_record.cached_payload or {})
        sample_age_seconds = stats_current_host_collectors.host_cpu_sample_age_seconds(sample, read_at)
        # The staleness threshold is NOT a literal here any more. It was a bare `3.0` while the
        # producer's cadence lived in stats_current as `1.0`: two copies of one policy, either of
        # which could move without the other noticing.
        stale = not advancing and stats_current_host_collectors.host_cpu_sample_is_stale(sample_age_seconds)
        status = "stale" if stale else ("warning" if record.warning_emitted else ("watching" if record.exceeded_since > 0 else "ok"))
        # `CpuBudgetRecord.current_percent` defaults to 0.0, so a server that has never
        # received a push published a confident `0.0%` that no sample stands behind. No
        # push ever arrived means `pushed_at == 0`, which is exactly `sample_age_seconds
        # is None` -- so the absence the record cannot express is recoverable here.
        never_sampled = sample_age_seconds is None
        return {
            "status": status,
            "current_percent": None if never_sampled else round(record.current_percent, 3),
            "budget_percent": SERVER_CPU_BUDGET_PERCENT,
            "sustained_budget_seconds": SERVER_CPU_BUDGET_SUSTAINED_SECONDS,
            "sustained_seconds": round(sustained_seconds, 3),
            "exceeded_since": record.exceeded_since,
            "last_warning_at": record.last_warning_at,
            "top_consumers": [dict(row) for row in record.top_consumers],
            "source": "statsd_push",
            "sample_age_seconds": None if sample_age_seconds is None else round(sample_age_seconds, 3),
            "stale": stale,
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

    def runtime_refresh_state(self, background_status: dict[str, Any], local_services: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.session_files_service.cache_lock:
            session_files_refreshing_count = len(self.session_files_service.work_records)
        with self.activity_transcript_service.tabber_cache_lock:
            tabber_activity_refreshing = self.activity_transcript_service.tabber_cache_record.refresh_worker is not None
            tabber_warmer_running = self.activity_transcript_service.tabber_warmer_record.running
        with self.activity_transcript_service.transcripts_payload_cache_lock:
            transcripts_payload_refreshing = self.activity_transcript_service.transcripts_payload_cache_record.worker is not None
        with self.client_watch_service.lock:
            dependency_invalidations = dict(self.client_watch_service.invalidation_counts)
            watcher_record = self.client_watch_service.event_watcher_record
        owner_invocations = self.client_watch_service.owner_invocation_snapshot()
        recurring_work = self.client_event_recurring_work_snapshot(watcher_record)
        client_event_snapshot = self.client_events.snapshot()
        heartbeat_attempts = int(client_event_snapshot.get("heartbeat_events") or 0)
        recurring_work.append({
            "owner": "sse_heartbeat",
            "class": "lease",
            "cadence_seconds": 15.0,
            "demanded": int(client_event_snapshot.get("subscribers") or 0) > 0,
            "attempts": heartbeat_attempts,
            "useful": heartbeat_attempts,
            "no_change": 0,
            "failures": 0,
            "last_attempt_at": float(client_event_snapshot.get("last_heartbeat_at") or 0.0),
            "last_useful_at": float(client_event_snapshot.get("last_heartbeat_at") or 0.0),
            "next_due_in_seconds": 15.0 if int(client_event_snapshot.get("subscribers") or 0) > 0 else 0.0,
        })
        recurring_work.append(self.update_check_recurring_work_snapshot())
        services = local_services.get("services") if isinstance(local_services, dict) else []
        approvald = next((service for service in services if isinstance(service, dict) and service.get("service") == "approvald"), {})
        approval_work = approvald.get("recurring_work") if isinstance(approvald.get("recurring_work"), dict) else {}
        recurring_work.append({
            "owner": "approvald_auto_approve",
            "class": str(approval_work.get("class") or "sample"),
            "cadence_seconds": float(approval_work.get("cadence_seconds") or 0.0),
            "demanded": bool(approval_work.get("demanded")),
            "attempts": int(approval_work.get("attempts") or 0),
            "useful": int(approval_work.get("useful") or 0),
            "no_change": int(approval_work.get("no_change") or 0),
            "failures": int(approval_work.get("failures") or 0),
            "last_attempt_at": float(approval_work.get("last_attempt_at") or 0.0),
            "last_useful_at": float(approval_work.get("last_useful_at") or 0.0),
            "next_due_in_seconds": float(approval_work.get("cadence_seconds") or 0.0) if approval_work.get("demanded") else 0.0,
        })
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
            # Bounded by trigger reason (fs_changed, transcripts_changed, ...), not by event volume:
            # how many jobd-product-backed refreshes the server-side watch loop actually published,
            # by the source that drove each one (checkbox 8/10 dependency-invalidation diagnostics).
            "dependency_invalidations": dependency_invalidations,
            "owner_invocations": owner_invocations,
            "recurring_work": recurring_work,
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

    @staticmethod
    def system_status_metric( value: object, *, running: bool, missing_state: str, missing_reason_code: str, missing_reason: str, ) -> dict[str, object]: # One envelope owner (`local_service_projection.measurement`), two callers: these # three process metrics and the M8 health metrics. The dict used to be built here, # which meant the health block would have been a second copy of the same shape.
        return SystemStatusProjector.system_status_metric(value, running=running, missing_state=missing_state, missing_reason_code=missing_reason_code, missing_reason=missing_reason)

    def system_status_service( self, row: dict[str, Any], *, health: local_service_projection.RetainedHealth | None = None, ) -> dict[str, Any]:
        if isinstance(self, type):
            return SystemStatusProjector.system_status_service(None, self, row, health=health)
        return system_status_projector_for(self).system_status_service(self, row, health=health)

    def stats_current_recovery_events(self, migration_status: dict[str, Any]) -> list[dict[str, str]]:
        return system_status_projector_for(self).stats_current_recovery_events(self, migration_status)

    def statsd_runtime_status(self) -> dict[str, Any]:
        return system_status_projector_for(self).statsd_runtime_status(self)

    def local_services_row_producers(self) -> dict[str, Callable[[], dict[str, Any]]]:
        return system_status_projector_for(self).local_services_row_producers(self)

    def local_services_recovery_entrypoints(self) -> dict[str, Callable[[], bool]]:
        return system_status_projector_for(self).local_services_recovery_entrypoints(self)

    def local_services_recovery_control(self) -> LocalServiceRecoveryControl:
        return system_status_projector_for(self).local_services_recovery_control(self)

    def local_services_recovery_events(self, rows: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
        return system_status_projector_for(self).local_services_recovery_events(self, rows)

    def local_services_snapshot(self) -> local_service_projection.LocalServicesSnapshot:
        return system_status_projector_for(self).local_services_snapshot(self)

    def attach_backend_health_store(self, store: Any) -> None:
        return system_status_projector_for(self).attach_backend_health_store(self, store)

    # Set by `cli.start_backend_health_observer` once the observer exists. `None` in every process
    # and every unit test that never armed one, which the projection reports as `observer_unattached`.
    backend_health_liveness_provider: Any = None

    def attach_backend_health_observer(self, observer: Any) -> None:
        return system_status_projector_for(self).attach_backend_health_observer(self, observer)

    def retained_backend_health(self) -> local_service_projection.RetainedHealth:
        return system_status_projector_for(self).retained_backend_health(self)

    def runtime_local_services(self) -> dict[str, Any]:
        return system_status_projector_for(self).runtime_local_services(self)

    def runtime_process_ledger(self) -> dict[str, Any]:
        return system_status_projector_for(self).runtime_process_ledger(self)

    def runtime_filesystem_batch_rows(self, metrics: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
        return system_status_projector_for(self).runtime_filesystem_batch_rows(self, metrics, limit)

    def runtime_control_report_payload(self) -> dict[str, Any]:
        return system_status_projector_for(self).runtime_control_report_payload(self)

    def runtime_report_core( self, *, background_status: dict[str, Any] | None = None, owner_control_response: dict[str, Any] | None = None, local_services: dict[str, Any] | None = None, ) -> dict[str, Any]:
        return system_status_projector_for(self).runtime_report_core(self, background_status=background_status, owner_control_response=owner_control_response, local_services=local_services)

    def runtime_report_advanced( self, *, background_status: dict[str, Any] | None = None, owner_debug: dict[str, Any] | None = None, owner_control_response: dict[str, Any] | None = None, force_transcripts: bool = True, local_services: dict[str, Any] | None = None, ) -> dict[str, Any]:
        return system_status_projector_for(self).runtime_report_advanced(self, background_status=background_status, owner_debug=owner_debug, owner_control_response=owner_control_response, force_transcripts=force_transcripts, local_services=local_services)

    def runtime_report_payload( self, *, background_status: dict[str, Any] | None = None, owner_debug: dict[str, Any] | None = None, owner_control_response: dict[str, Any] | None = None, force_transcripts: bool = True, ) -> dict[str, Any]:
        return system_status_projector_for(self).runtime_report_payload(self, background_status=background_status, owner_debug=owner_debug, owner_control_response=owner_control_response, force_transcripts=force_transcripts)

    def system_status_server_block(self, sample: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        return system_status_projector_for(self).system_status_server_block(self, sample, now=now)

    def system_status_core_payload(self) -> dict[str, Any]:
        return system_status_projector_for(self).system_status_core_payload(self)

    def system_status_advanced_payload(self) -> dict[str, Any]:
        return system_status_projector_for(self).system_status_advanced_payload(self)

    def system_status_payload(self) -> dict[str, Any]:
        return system_status_projector_for(self).system_status_payload(self)

    # ---- the background snapshot owner -------------------------------------------------------
    #
    # Set by `TmuxWebtermHTTPServer.__init__` through `start_system_status_snapshot_owner`, and
    # stopped by `server_close`. `None` in a unit test that never armed one, which the route
    # reports as an explicitly typed refusal rather than by rebuilding on the request thread.
    client_watch_service = OwnedStateAttribute("_watch_bridge", "state")
    session_files_service = OwnedStateAttribute("_session_files_coordinator", "state")
    activity_transcript_service = OwnedStateAttribute("_activity_cache", "state")
    _watched_pr_truncated_signature = OwnedStateAttribute("_activity_cache", "watched_pr_truncated_signature")
    backend_health_store = OwnedStateAttribute("_system_status_projector", "backend_health_store")
    backend_health_liveness_provider = OwnedStateAttribute("_system_status_projector", "backend_health_liveness_provider")
    system_status_snapshot = OwnedStateAttribute("_system_status_projector", "snapshot")

    def attach_system_status_snapshot_owner(self, owner: system_status_snapshot_module.SystemStatusSnapshotOwner) -> None:
        return system_status_projector_for(self).attach_system_status_snapshot_owner(self, owner)

    def start_system_status_snapshot_owner(self) -> bool:
        return system_status_projector_for(self).start_system_status_snapshot_owner(self)

    def stop_system_status_snapshot_owner(self) -> None:
        return system_status_projector_for(self).stop_system_status_snapshot_owner(self)

    def report_system_status_snapshot_failure(self, slot: str, error: BaseException) -> None:
        return system_status_projector_for(self).report_system_status_snapshot_failure(self, slot, error)

    def system_status_snapshot_response(self, *, advanced: bool = False) -> tuple[bytes, Mapping[str, Any]]:
        return system_status_projector_for(self).system_status_snapshot_response(self, advanced=advanced)

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
        if activity_summary_enabled() and not session and len(results) < limit and hasattr(self, "activity_summary_lock"):
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
        self.request_tabber_activity_refresh("input-heartbeat")

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

    def tabber_activity_session_source_signature( self, info: SessionInfo, files_payload: SessionFilesPayload | dict[str, Any], activity_snapshot: dict[str, Any], preclassified_by_target: dict[str, dict[str, Any]], attention_ack_rev: int, owned_rows_for_session: dict[tuple[str, str, str], dict[str, Any]] | None = None, ) -> str:
        return self._activity_cache.tabber_activity_session_source_signature(self, info, files_payload, activity_snapshot, preclassified_by_target, attention_ack_rev, owned_rows_for_session)

    def tabber_activity_view_coalesce_identity(self, scope: str, bounded_hours: float, source_signature: str) -> tuple[str, int]:
        return self._activity_cache.tabber_activity_view_coalesce_identity(self, scope, bounded_hours, source_signature)

    def compute_tabber_activity_rows_via_jobd( self, changed_sessions: dict[str, SessionInfo], *, discovered_sessions: dict[str, SessionInfo], session_files_by_session: dict[str, Any], activity_snapshot: dict[str, Any], preclassified_by_session: dict[str, dict[str, dict[str, Any]]], owned_agent_rows: dict[tuple[str, str, str], dict[str, Any]], snapshot_revision: int, scope: str, bounded_hours: float, source_signature: str, locale: str = "en", ) -> dict[str, dict[str, Any]]:
        return self._activity_cache.compute_tabber_activity_rows_via_jobd(self, changed_sessions, discovered_sessions=discovered_sessions, session_files_by_session=session_files_by_session, activity_snapshot=activity_snapshot, preclassified_by_session=preclassified_by_session, owned_agent_rows=owned_agent_rows, snapshot_revision=snapshot_revision, scope=scope, bounded_hours=bounded_hours, source_signature=source_signature, locale=locale)

    def build_activity_payload(self, session_scope: Any = "configured", hours: Any = 24.0) -> dict[str, Any]:
        return self._activity_cache.build_activity_payload(self, session_scope, hours)

    def tabber_activity_source_signature(self, session_scope: Any = "configured") -> str: # Acknowledgements change agent-window visibility without changing the process or # transcript identity below. Fold the durable revision into this cache key so every # server stops serving an earlier unacknowledged Tabber snapshot immediately.
        return self._activity_cache.tabber_activity_source_signature(self, session_scope)

    def tabber_activity_cache_disk_path(self, hours: float, source_signature: str = "") -> tuple[Path, str]: # A source signature fences freshness inside the record; it must not become # part of the filename. Statusd revisions can legitimately advance while a # Tabber refresh is in flight, and the old design left one durable file per # short-lived signature, then made followers see an empty cache miss.
        return self._activity_cache.tabber_activity_cache_disk_path(self, hours, source_signature)

    def tabber_activity_cache_manifest_path(self, signature: str) -> Path:
        return self._activity_cache.tabber_activity_cache_manifest_path(self, signature)

    def read_tabber_activity_disk_cache( self, hours: float, max_age_seconds: float | None = None, allow_stale: bool = True, source_signature: str = "", allow_source_mismatch: bool = False, ) -> tuple[dict[str, Any], bool, float] | None:
        return self._activity_cache.read_tabber_activity_disk_cache(self, hours, max_age_seconds, allow_stale, source_signature, allow_source_mismatch)

    def tabber_published_cache(self):
        return self._activity_cache.tabber_published_cache(self)

    def write_tabber_activity_disk_cache_unlocked(self, path: Path, signature: str, payload: dict[str, Any], source_signature: str) -> None:
        return self._activity_cache.write_tabber_activity_disk_cache_unlocked(self, path, signature, payload, source_signature)

    def write_tabber_activity_disk_cache(self, payload: dict[str, Any], source_signature: str = "") -> None:
        return self._activity_cache.write_tabber_activity_disk_cache(self, payload, source_signature)

    def set_tabber_activity_cache(self, payload: dict[str, Any], stored_at: float | None = None, write_disk: bool = True, source_signature: str = "") -> None:
        return self._activity_cache.set_tabber_activity_cache(self, payload, stored_at, write_disk, source_signature)

    def get_tabber_activity_cache( self, max_age_seconds: float, allow_stale: bool = True, hours: float | None = None, source_signature: str = "", allow_source_mismatch: bool = False, ) -> tuple[dict[str, Any], bool, float] | None:
        return self._activity_cache.get_tabber_activity_cache(self, max_age_seconds, allow_stale, hours, source_signature, allow_source_mismatch)

    def refresh_tabber_activity_cache(self, hours: Any = 24.0) -> dict[str, Any]:
        return self._activity_cache.refresh_tabber_activity_cache(self, hours)

    def refresh_tabber_activity_cache_owner(self, bounded_hours: float, source_signature: str) -> dict[str, Any]:
        return self._activity_cache.refresh_tabber_activity_cache_owner(self, bounded_hours, source_signature)

    def publish_tabber_activity_refresh_if_changed(self, *, compute_ms: float) -> bool:
        return self._activity_cache.publish_tabber_activity_refresh_if_changed(self, compute_ms=compute_ms)

    def run_tabber_activity_cache_refresh(self, worker: threading.Thread) -> None:
        return self._activity_cache.run_tabber_activity_cache_refresh(self, worker)

    def start_tabber_activity_cache_refresh(self) -> bool:
        return self._activity_cache.start_tabber_activity_cache_refresh(self)

    def start_tabber_activity_cache_warmer(self) -> bool:
        return composed_owner_for(self, "_activity_cache", ActivityCache).start_tabber_activity_cache_warmer(self)

    def tabber_activity_cache_warmer_loop(self, record: TabberActivityWarmerRecord) -> None:
        return composed_owner_for(self, "_activity_cache", ActivityCache).tabber_activity_cache_warmer_loop(self, record)

    def empty_tabber_activity_payload(self, bounded_hours: float, refresh_seconds: float, **cache: Any) -> dict[str, Any]:
        return self._activity_cache.empty_tabber_activity_payload(self, bounded_hours, refresh_seconds, **cache)

    def activity_payload(self, hours: Any = 24.0, visible: bool = True) -> tuple[dict[str, Any], HTTPStatus]:
        return self._activity_cache.activity_payload(self, hours, visible)

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

    def session_files_payload( self, session: str | None = None, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, requester: str = "api-session-files", accepted_operation: bool = False, ) -> tuple[SessionFilesPayload, HTTPStatus]:
        return self._session_files_coordinator.session_files_payload(self, session, hours, from_ref, to_ref, repo_refs, force, requester, accepted_operation)

    def session_files_http_payload( self, session: str | None = None, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._session_files_coordinator.session_files_http_payload(self, session, hours, from_ref, to_ref, repo_refs, force)

    def session_files_batch_payload( self, sessions: list[str] | None = None, hours: float = 24.0, from_ref: str | None = None, to_ref: str | None = None, repo_refs: dict[str, dict[str, str]] | None = None, force: bool = False, ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._session_files_coordinator.session_files_batch_payload(self, sessions, hours, from_ref, to_ref, repo_refs, force)

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
        if action == "stats_cpu_sample":
            sample = request.get("sample")
            if not isinstance(sample, dict):
                return {"ok": False, "error": "invalid stats CPU sample"}
            try:
                normalized = {
                    "time": float(sample["time"]),
                    "pid": int(sample["pid"]),
                    "cpu_percent": max(0.0, float(sample["cpu_percent"])),
                    "system_cpu_percent": max(0.0, float(sample["system_cpu_percent"])),
                    "rss_bytes": max(0, int(sample["rss_bytes"])),
                }
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "error": "invalid stats CPU sample"}
            if normalized["pid"] != os.getpid():
                return {"ok": False, "error": "stats CPU sample PID mismatch"}
            normalized["started_at"] = SERVER_STARTED_AT
            # No `uptime_seconds` here. A push used to stamp one, which made the panel's uptime a
            # CACHED value that froze at the last delivered sample while still reading `measured`.
            # Uptime is derived at render time in `system_status_server_block` from this process's
            # own clock; a delivered sample has nothing to say about how long this process has run.
            with self.stats_collection_state.sample_lock:
                record = self.stats_collection_state.sample_record
                record.cached_monotonic = time.monotonic()
                record.cached_payload = normalized
            budget = self.update_server_cpu_budget(normalized)
            return {"ok": True, "cpu_budget": budget}
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
        if action == "runtime_measurement_metrics":
            scope = str(request.get("scope") or "")
            if scope != "capture":
                return {"ok": False, "error": "unsupported measurement scope"}
            return {"ok": True, "performance": self.performance_metrics_payload(measurement_scope=scope)}
        if action == "background_ping":
            return {"ok": True, "status": self.background_owner.status_payload()}
        if action == "runtime_report":
            # Serves --print-runtime-report over the existing control socket so the
            # CLI never constructs a second TmuxWebtermApp (whose startup could
            # stall on an overloaded host) just to render this JSON.
            return {"ok": True, "report": self.runtime_control_report_payload()}
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

    @property
    def server_epoch(self) -> str:
        """The identity of THIS server process, as every client already knows it.

        The client-event broker already mints one per process and stamps it on every envelope, and
        the browser already resets its per-process state when that value changes. Anything else that
        needs "which server produced this" reads it from here rather than minting a second epoch,
        so one process can never present two identities to the same browser.

        The epoch is an opaque equality partition, never an ordering. Two generations are comparable
        only when their epochs are equal; nothing may infer that one epoch came after another.
        """

        return self.client_events.epoch

    def metadata_identity(self, generation: int) -> dict[str, Any]:
        """The one identity object every metadata path ships: which server, which build."""

        return {"epoch": self.server_epoch, "generation": max(0, int(generation))}

    def stamp_metadata_identity(self, payload: dict[str, Any], generation: int) -> dict[str, Any]:
        """Project the identity into a metadata payload, deriving the legacy scalar from it.

        One projector for every metadata path -- the committed cache, the cold lightweight response,
        the cache hit, the `/api/transcripts` alias, and the payload pushed on
        `transcripts_changed`. `metadata_generation` remains only as a projection of
        `metadata_identity`, so no consumer can assemble an identity out of two fields that a
        server restart moves independently.
        """

        identity = self.metadata_identity(generation)
        payload["metadata_identity"] = identity
        payload["metadata_generation"] = identity["generation"]
        return identity

    def start_metadata_refresh_for_request(
        self,
        requested_at: float | None,
        *,
        publish: bool,
        defer: bool = False,
    ) -> tuple[bool, int]:
        """Start (or queue) a build that observes ``requested_at``, and name the generation that will.

        Returns ``(refreshing, pending_generation)``. A pending generation of 0 means no build was
        accepted or queued for this caller: it is NOT a build identity and must never be published
        to a forced caller as one, because zero is already satisfied by everything.

        The guard decides which build answers this caller and reports it through
        ``pending_generation_out`` under the lock that made the decision. This used to re-read the
        record afterwards, so an in-flight build that already observed the request could commit and
        release the guard in between; the record then showed no worker and no queued rebuild, and a
        caller that had in fact just been answered was told ``no_build_accepted``.
        """

        pending: list[int] = []
        started = self.start_transcripts_payload_refresh(
            publish=publish,
            defer=defer,
            not_before=requested_at,
            pending_generation_out=pending,
        )
        pending_generation = pending[0]
        return started or pending_generation > 0, pending_generation

    def forced_metadata_pending_cache_fields(self, pending_generation: int) -> dict[str, Any]:
        """The forced-read half of the contract: the identity the caller must wait for, or why not.

        Only a forced refresh publishes its result, so only a forced refresh may name a generation
        for a client to wait on. Naming one that is never published would replace a stale render
        with a wait that cannot end; naming zero would replace it with a wait that ends instantly
        against bytes that predate the request.
        """

        if pending_generation <= 0:
            return {"pending_generation": 0, "pending_identity": None, "pending_error": "no_build_accepted"}
        return {
            "pending_generation": int(pending_generation),
            "pending_identity": self.metadata_identity(pending_generation),
        }

    def build_session_metadata_payload(self, lightweight: bool = False) -> dict[str, Any]:
        refresh_errors = self.refresh_sessions(maintenance=not lightweight)
        sessions, errors = discover_sessions(self.sessions)
        with metadata_build_cache():
            session_payloads = {
                name: session_to_json(
                    info,
                    self.metadata_cache,
                    allow_network=False,
                    include_metadata=not lightweight,
                    work_graph=self.session_work_graph_for_generation(info) if not lightweight else None,
                )
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
        # An unbuilt payload still carries the epoch: a client must be able to tell WHICH server's
        # generation zero this is before it decides whether zero is older than what it already has.
        self.stamp_metadata_identity(payload, 0)
        if not lightweight:
            self.apply_metadata_badge_pulses(session_payloads)
            self.warm_metadata_cache_async(sessions)
        return payload

    def session_work_graph_source_generation(
        self,
        info: SessionInfo,
        graph: dict[str, Any] | None,
    ) -> tuple[Any, ...]:
        """Name every source that can change one session's canonical work graph."""
        session_generation = self.client_event_payload_signature(asdict(info))
        repository_generations = self.metadata_warm_repository_signature(graph)
        provider_generation = self.metadata_cache.source_generation()
        worktrees = graph.get("git_worktrees") if isinstance(graph, dict) else None
        uncovered_repository = any(
            isinstance(worktree, dict)
            and bool(worktree.get("root"))
            and not self.watcher_covers_repo(Path(str(worktree["root"])))
            for worktree in worktrees.values()
        ) if isinstance(worktrees, dict) else False
        fallback_generation = int(time.monotonic() // GIT_METADATA_CACHE_SECONDS) if uncovered_repository else 0
        return session_generation, repository_generations, provider_generation, fallback_generation

    def session_work_graph_for_generation(self, info: SessionInfo) -> dict[str, Any]:
        """Return the one cached graph for an explicit session/repository/provider generation."""
        service = self.activity_transcript_service
        with service.work_graph_cache_lock:
            cached = service.work_graph_cache.get(info.session)
            cached_graph = cached[1] if cached is not None else None
            source_generation = self.session_work_graph_source_generation(info, cached_graph)
            if cached is not None and cached[0] == source_generation:
                return copy.deepcopy(cached_graph)
            future_key = (info.session, source_generation)
            future = service.work_graph_futures.get(future_key)
            if future is None:
                future = Future()
                service.work_graph_futures[future_key] = future
                owner = True
            else:
                owner = False

        if not owner:
            return copy.deepcopy(future.result())

        try:
            self.client_watch_service.note_owner_invocation("jobd_work_graph_rebuild")
            graph = session_work_graph(info, self.metadata_cache, allow_network=False)
            completed_generation = self.session_work_graph_source_generation(info, graph)
            stable_during_build = (
                source_generation[0] == completed_generation[0]
                and source_generation[2] == completed_generation[2]
                and (cached is None or source_generation[1:] == completed_generation[1:])
            )
            with service.work_graph_cache_lock:
                if stable_during_build and service.work_graph_cache.get(info.session) is cached:
                    service.work_graph_cache[info.session] = (completed_generation, copy.deepcopy(graph))
                active_sessions = set(self.sessions)
                for session in list(service.work_graph_cache):
                    if session not in active_sessions:
                        service.work_graph_cache.pop(session, None)
            future.set_result(graph)
            return graph
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with service.work_graph_cache_lock:
                if service.work_graph_futures.get(future_key) is future:
                    service.work_graph_futures.pop(future_key, None)

    def indexed_repo_roots_snapshot(self) -> list[str]:
        """Return the last jobd discovery immediately and advance it asynchronously."""
        settings = self.settings_payload().get("settings", {})
        file_explorer = settings.get("file_explorer", {}) if isinstance(settings, dict) else {}
        indexed_dirs = self.indexed_repo_discovery_dirs(file_explorer)
        service = self.activity_transcript_service
        now = time.monotonic()
        with service.indexed_repo_lock:
            record = service.indexed_repo_record
            if record.indexed_dirs != indexed_dirs:
                record.indexed_dirs = indexed_dirs
                record.roots = []
                record.refreshed_at = 0.0
                record.retry_at = 0.0
                record.root_generations = {root: 0 for root in indexed_dirs}
                record.completed_generation_signature = ()
            else:
                for root in indexed_dirs:
                    record.root_generations.setdefault(root, 0)
            generation_signature = tuple((root, record.root_generations[root]) for root in indexed_dirs)
            watcher_healthy = self.indexed_repo_discovery_watcher_healthy()
            roots = list(record.roots)
            should_start = (
                record.worker is None
                and now >= record.retry_at
                and (
                    record.completed_generation_signature != generation_signature
                    or (not watcher_healthy and (record.refreshed_at <= 0.0 or now - record.refreshed_at >= INDEXED_REPO_ROOTS_CACHE_SECONDS))
                )
            )
            if should_start:
                worker = threading.Thread(
                    target=self.refresh_indexed_repo_roots_worker,
                    args=(indexed_dirs, generation_signature),
                    name="yolomux-indexed-repos",
                    daemon=True,
                )
                record.worker = worker
                worker.start()
        return roots

    @staticmethod
    def indexed_repo_discovery_dirs(file_explorer: Any) -> tuple[str, ...]:
        raw_dirs = file_explorer.get("indexed_dirs", []) if isinstance(file_explorer, dict) else []
        if not isinstance(raw_dirs, list):
            return ()
        return tuple(sorted({str(Path(item).expanduser().resolve(strict=False)) for item in raw_dirs if isinstance(item, str) and str(item).strip()}))

    def indexed_repo_discovery_watcher_healthy(self) -> bool:
        client_watch_service = self.__dict__.get("client_watch_service")
        if client_watch_service is None:
            return False
        with client_watch_service.lock:
            return client_watch_service.event_watcher_record.filesystem_healthy

    def mark_indexed_repo_discovery_dirty(self, changed_paths: list[Path]) -> None:
        service = self.activity_transcript_service
        with service.indexed_repo_lock:
            record = service.indexed_repo_record
            for root in record.indexed_dirs:
                root_path = Path(root)
                if any(filesystem_paths_intersect(root_path, path) for path in changed_paths):
                    record.root_generations[root] = record.root_generations.get(root, 0) + 1

    def refresh_indexed_repo_roots_worker(self, indexed_dirs: tuple[str, ...], generation_signature: tuple[tuple[str, int], ...]) -> None:
        """Submit and observe one discovery job without blocking metadata requests."""
        service = self.activity_transcript_service
        worker = threading.current_thread()
        succeeded = False
        try:
            signature = json.dumps(generation_signature, separators=(",", ":")).encode("utf-8")
            generation = max(1, int(hashlib.sha256(signature).hexdigest()[:12], 16))
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
                if service.indexed_repo_record.indexed_dirs != indexed_dirs or tuple((root, service.indexed_repo_record.root_generations.get(root, 0)) for root in indexed_dirs) != generation_signature:
                    return
                service.indexed_repo_record.job_id = job_id
            while True:
                with service.indexed_repo_lock:
                    if service.indexed_repo_record.indexed_dirs != indexed_dirs or tuple((root, service.indexed_repo_record.root_generations.get(root, 0)) for root in indexed_dirs) != generation_signature:
                        return
                response = self.job_client.result(job_id)
                job = response.get("job") if isinstance(response.get("job"), dict) else {}
                status = str(job.get("status") or "")
                if status == "completed" and isinstance(job.get("result"), dict):
                    roots = job["result"].get("roots")
                    safe_roots = [str(item) for item in roots if isinstance(item, str)] if isinstance(roots, list) else []
                    with service.indexed_repo_lock:
                        if service.indexed_repo_record.indexed_dirs == indexed_dirs and tuple((root, service.indexed_repo_record.root_generations.get(root, 0)) for root in indexed_dirs) == generation_signature:
                            service.indexed_repo_record.roots = safe_roots
                            service.indexed_repo_record.refreshed_at = time.monotonic()
                            service.indexed_repo_record.completed_generation_signature = generation_signature
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
            "agentAuth": agent_auth_status_payload(agent_auth_status(force=True)) if force else cached_agent_auth_status_snapshot(),
            "availableAgents": available_agent_commands(),
        }

    def session_metadata_payload(self, force: bool = False) -> dict[str, Any]:
        max_age = TRANSCRIPTS_PAYLOAD_CACHE_SECONDS
        # A forced read is answered from the cache, so the bytes it returns are always older than
        # the request. Name the generation the caller must wait for, and guarantee that a build
        # which observes state as of this instant is actually started -- never coalesced onto one
        # that began earlier and therefore cannot contain what the caller is asking about.
        requested_at = time.monotonic()
        cached = self.get_transcripts_payload_cache(max_age, allow_stale=True)
        if cached:
            payload, fresh, age_seconds = cached
            payload["cache"] = {
                "hit": True,
                "stale": force or not fresh,
                "age_seconds": round(age_seconds, 3),
                "refresh_seconds": max_age,
                "generation": int(payload.get("metadata_generation") or 0),
            }
            if force or not fresh:
                refreshing, pending_generation = self.start_metadata_refresh_for_request(
                    requested_at if force else None,
                    publish=force,
                )
                payload["cache"]["refreshing"] = refreshing
                if force:
                    payload["cache"].update(self.forced_metadata_pending_cache_fields(pending_generation))
            return payload
        payload = self.build_session_metadata_payload(lightweight=True)
        # A cold miss is the one case where the response carries nothing built at all, so it is also
        # the case where a forced caller most needs the identity of the build that will answer it.
        # Emitting no pending identity here handed the browser target zero, which every payload
        # already satisfies: the force resolved instantly against a lightweight payload built before
        # the mutation it was checking for.
        refreshing, pending_generation = self.start_metadata_refresh_for_request(
            requested_at if force else None,
            publish=True,
            defer=not force,
        )
        payload["cache"] = {
            "hit": False,
            "stale": True,
            "age_seconds": 0,
            "refresh_seconds": max_age,
            "refreshing": refreshing,
            "lightweight": True,
            "generation": 0,
        }
        if force:
            payload["cache"].update(self.forced_metadata_pending_cache_fields(pending_generation))
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
            record = self.metadata_warm_record
            record.worker = worker
            record.stop_event = stop_event

            def rollback() -> None:
                # Thread.start failed while metadata_warm_lock is still held by this caller; clear
                # the just-published worker in place so no observer ever joins an unstarted thread.
                # Do NOT reacquire metadata_warm_lock here — this is a plain Lock and re-entry would
                # deadlock the very caller performing the rollback.
                if self.metadata_warm_record is record and record.worker is worker:
                    record.stop_event.set()
                    record.worker = None

            # Publish-and-start under one lock hold: a teardown that acquires metadata_warm_lock in
            # the gap can only see a not-yet-published record or a worker that is already started.
            common.start_thread_with_rollback(worker, rollback)

    def metadata_warm_view_coalesce_identity(self, source_signature: str) -> tuple[str, int]:
        """Cross-port product identity for `metadata_warm_view`, so two web ports warming the same
        unchanged session set dedupe to one worker execution instead of two GitHub/Linear round trips."""
        coalesce_key = f"metadata_warm:{source_signature}"[:256]
        generation = int(hashlib.sha256(source_signature.encode("utf-8")).hexdigest()[:12], 16)
        return coalesce_key, generation

    def metadata_warm_repository_signature(self, graph: dict[str, Any] | None) -> tuple[tuple[str, int], ...]:
        """Fold watched repository mutations into a session's otherwise stable warm identity."""
        worktrees = graph.get("git_worktrees") if isinstance(graph, dict) else None
        if not isinstance(worktrees, dict):
            return ()
        rows: list[tuple[str, int]] = []
        for worktree in worktrees.values():
            root = str(worktree.get("root") or "") if isinstance(worktree, dict) else ""
            if root and self.watcher_covers_repo(root):
                rows.append((root, self.repo_dirty_generation(root)))
        return tuple(sorted(set(rows)))

    def metadata_warm_source_signature(
        self,
        sessions: dict[str, SessionInfo],
        repository_generations: tuple[tuple[str, int], ...] = (),
    ) -> str:
        """Return the jobd product identity for metadata work, excluding UI-only churn.

        The worker receives complete session records to do its work, but its cross-port product
        key must follow the same repository-relevant identity used by the in-process warm cache.
        Otherwise a status transition on a second web port submits another GitHub/Linear warm.
        The caller supplies already-known watched repository generations from its graph cache,
        fencing a real filesystem change without making a fresh Git discovery on this hot path.
        """
        rows = tuple((name, metadata_warm_session_signature(info)) for name, info in sorted(sessions.items()))
        return self.client_event_payload_signature((rows, repository_generations))

    def warm_metadata_cache_via_jobd(
        self,
        sessions: dict[str, SessionInfo],
        repository_generations: tuple[tuple[str, int], ...] = (),
    ) -> None:
        """Materialize GitHub/Linear/git metadata for `sessions` in a jobd worker, then replay the
        returned cache entries into `self.metadata_cache`.

        ALL network calls and git spawns happen in the jobd worker. Raises
        `MetadataWarmJobdUnavailable` (never falls back to an inline network fetch) so the caller
        skips this warm cycle and the next periodic warm retries.
        """
        sessions_payload = {name: asdict(info) for name, info in sessions.items()}
        source_signature = self.metadata_warm_source_signature(sessions, repository_generations)
        coalesce_key, generation = self.metadata_warm_view_coalesce_identity(source_signature)
        response = self.job_client.submit(
            "metadata_warm_view",
            {"sessions": sessions_payload},
            priority="maintenance",
            generation=generation,
            coalesce_key=coalesce_key,
            deadline_ms=METADATA_WARM_JOBD_JOB_DEADLINE_MS,
        )
        if not response.get("ok"):
            raise MetadataWarmJobdUnavailable(str(response.get("error") or "jobd submit rejected"))
        with self.metadata_warm_lock:
            stop_event = self.metadata_warm_record.stop_event
        try:
            _meta, body, state = wait_for_jobd_product(
                self.job_client,
                coalesce_key,
                generation,
                METADATA_WARM_JOBD_WAIT_SECONDS,
                stop_event=stop_event,
            )
        except JobdProductRpcUnavailable as error:
            raise MetadataWarmJobdUnavailable(str(error)) from error
        if body is None:
            raise MetadataWarmJobdUnavailable(f"jobd product not ready (state={state or 'none'})")
        data = json.loads(body.decode("utf-8"))
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            raise MetadataWarmJobdUnavailable("malformed jobd metadata-warm product")
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            self.metadata_cache.set(key, entry.get("value"), ttl=self.float_value(entry.get("ttl_remaining"), 0.0))

    def warm_metadata_cache(self, sessions: dict[str, SessionInfo], stop_event: threading.Event) -> None:
        refresh_needed = False
        try:
            with metadata_build_cache():
                for info in sessions.values():
                    if stop_event.is_set():
                        break
                    cached_graph = self.cached_transcripts_work_graph(info.session)
                    signature = (metadata_warm_session_signature(info), self.metadata_warm_repository_signature(cached_graph))
                    now = time.monotonic()
                    with self.metadata_warm_lock:
                        completion = self.metadata_warm_record.completed.get(info.session)
                    if completion is not None and completion[0] == signature and completion[1] > now:
                        continue
                    # One jobd round trip per session (not one batched submission for the whole
                    # sessions dict) so a demotion between sessions stops here, before the NEXT
                    # session's network/git work is ever submitted -- the same between-session
                    # granularity the old inline loop had.
                    try:
                        self.warm_metadata_cache_via_jobd({info.session: info}, signature[1])
                    except MetadataWarmJobdUnavailable as exc:
                        logger.info("metadata warm deferred (jobd) for session %s: %s", info.session, exc)
                    else:
                        with self.metadata_warm_lock:
                            self.metadata_warm_record.completed[info.session] = (
                                signature,
                                time.monotonic() + common.METADATA_CACHE_TTL_SECONDS,
                            )
                    if stop_event.is_set():
                        break
                    # The foreground payload intentionally avoids GitHub work. Once the jobd warm
                    # above fills the cache, rebuild only when the canonical graph actually changed;
                    # otherwise a warm build would leave YO!info showing its stale no-PR graph
                    # until a later unrelated refresh, or continuously schedule itself.
                    enriched_graph = session_work_graph(info, self.metadata_cache, allow_network=False)
                    if isinstance(cached_graph, dict) and self.work_graph_refresh_signature(cached_graph) != self.work_graph_refresh_signature(enriched_graph):
                        refresh_needed = True
        except (OSError, RuntimeError, ValueError) as exc:
            self.log_event(None, "metadata_warm_failed", str(exc)[:512], {"error": type(exc).__name__})
        finally:
            with self.metadata_warm_lock:
                if self.metadata_warm_record.worker is threading.current_thread():
                    self.metadata_warm_record.worker = None
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
                self.client_watch_service.note_owner_invocation("transcript_tail_scan")
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

    def transcript_compact_view_result(
        self,
        session: str,
        messages: int,
        *,
        compact_lines: int = 0,
        since: datetime | None = None,
        info: SessionInfo | None = None,
        agent_override: AgentInfo | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus, TranscriptProductOperation | None]:
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
            return {"session": session, "errors": errors, **user_message_payload("transcript.noAgentFound", diagnostic)}, HTTPStatus.NOT_FOUND, None
        agent = agent_override or next((item for item in info.agents if item.transcript), info.agents[0])
        if not agent.transcript:
            diagnostic = str(agent.error or "no agent transcript found")
            return {"session": session, "agent": asdict(agent), "errors": errors, **user_message_payload("transcript.error.unavailable", diagnostic, error=diagnostic)}, HTTPStatus.NOT_FOUND, None
        path = Path(agent.transcript).expanduser()
        try:
            generation = file_stat_signature(path)
        except OSError as exc:
            diagnostic = str(exc)
            return {"session": session, "agent": asdict(agent), **user_message_payload("transcript.error.readFailed", diagnostic, error=diagnostic)}, HTTPStatus.INTERNAL_SERVER_ERROR, None
        safe_messages = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        safe_lines = max(0, min(compact_lines, MAX_COMPACT_TRANSCRIPT_ITEMS))
        stable_identity = transcript_cache_identity(str(path))
        since_text = since.astimezone(timezone.utc).isoformat() if since is not None else ""
        expected_identity = [int(stable_identity[1]), int(stable_identity[2])]
        # Fold the parser generation into the memory cache key AND the jobd keys so a parser-shape
        # change (bumped TRANSCRIPT_PARSER_GENERATION) busts every previously cached entry/product.
        cache_key = (TRANSCRIPT_PARSER_GENERATION, stable_identity, generation, safe_messages, safe_lines, str(agent.kind or ""), since_text)
        # The product key is deliberately byte-generation-STRIPPED (no mtime/size). mtime+size busts
        # the exact cache key on every append, so an active transcript would otherwise stay `pending`
        # forever; keying the last-known-good product by stable file identity + shape lets a newer
        # append supersede older queued parses (via the byte-derived generation number) while the
        # prior complete product is still served stale-while-revalidate.
        product_key = f"transcript:v{TRANSCRIPT_PARSER_GENERATION}:{stable_identity}:{safe_messages}:{safe_lines}:{since_text}"
        service = self.activity_transcript_service
        with service.transcript_job_cache_lock:
            cached = service.transcript_job_cache.get(cache_key)
        if cached is None:
            generation_number = (int(generation[1]) ^ int(generation[2])) & ((1 << 63) - 1)
            request, product_body = self.job_client.produce(
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
                coalesce_key=product_key,
                deadline_ms=15_000,
                delivery="ready_or_receipt",
                allow_stale=True,
            )
            job = request.get("job") if isinstance(request.get("job"), dict) else {}
            job_id = str(job.get("job_id") or "")
            operation = TranscriptProductOperation(
                job_id=job_id,
                product_key=product_key,
                generation=generation_number,
                cache_key=cache_key,
                expected_generation=(int(generation[1]), int(generation[2])),
                expected_identity=(int(expected_identity[0]), int(expected_identity[1])),
            ) if request.get("ok") and job_id else None
            if operation is not None:
                with service.transcript_job_cache_lock:
                    service.transcript_job_records[cache_key] = operation.job_id
            product = self.decode_transcript_product(product_body, expected_identity)
            if product is not None:
                exact = self.transcript_product_result_matches(
                    product,
                    (int(generation[1]), int(generation[2])),
                    (int(expected_identity[0]), int(expected_identity[1])),
                )
                if exact:
                    if operation is not None:
                        self.cache_transcript_product_result(operation, product)
                    else:
                        with service.transcript_job_cache_lock:
                            self.cache_set_limited(
                                service.transcript_job_cache,
                                cache_key,
                                dict(product),
                                CONTEXT_ITEMS_CACHE_MAX_ITEMS,
                            )
                return {
                    "session": session,
                    "path": str(path),
                    "messages": safe_messages,
                    "compact_lines": list(product.get("compact_lines") or []),
                    "items": copy.deepcopy(product.get("items") or []),
                    "since_items": copy.deepcopy(product.get("since_items") or []),
                    "since_stats": dict(product.get("since_stats") or {}),
                    "pending": False,
                    "stale": not exact,
                    "agent": asdict(agent),
                    "errors": errors,
                }, HTTPStatus.OK, None
            return {
                "session": session,
                "path": str(path),
                "messages": safe_messages,
                "compact_lines": [],
                "items": [],
                "since_items": [],
                "since_stats": {},
                "pending": True,
                "stale": False,
                "agent": asdict(agent),
                "errors": errors,
            }, HTTPStatus.OK, operation
        return {
            "session": session,
            "path": str(path),
            "messages": safe_messages,
            "compact_lines": list(cached.get("compact_lines") or []),
            "items": copy.deepcopy(cached.get("items") or []),
            "since_items": copy.deepcopy(cached.get("since_items") or []),
            "since_stats": dict(cached.get("since_stats") or {}),
            "pending": False,
            "stale": False,
            "agent": asdict(agent),
            "errors": errors,
        }, HTTPStatus.OK, None

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
        payload, status, _operation = self.transcript_compact_view_result(
            session,
            messages,
            compact_lines=compact_lines,
            since=since,
            info=info,
            agent_override=agent_override,
        )
        return payload, status

    @staticmethod
    def decode_transcript_product(body: bytes, expected_identity: list[int]) -> dict[str, Any] | None:
        if not body:
            return None
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(decoded, dict):
            return None
        if "identity" in decoded and decoded.get("identity") != expected_identity:
            return None
        return decoded

    def cache_transcript_product_result(
        self,
        operation: TranscriptProductOperation,
        result: dict[str, Any],
    ) -> bool:
        exact = self.transcript_product_result_matches(
            result,
            operation.expected_generation,
            operation.expected_identity,
        )
        service = self.activity_transcript_service
        with service.transcript_job_cache_lock:
            if service.transcript_job_records.get(operation.cache_key) == operation.job_id:
                service.transcript_job_records.pop(operation.cache_key, None)
            if exact:
                self.cache_set_limited(
                    service.transcript_job_cache,
                    operation.cache_key,
                    dict(result),
                    CONTEXT_ITEMS_CACHE_MAX_ITEMS,
                )
        return exact

    @staticmethod
    def transcript_product_result_matches(
        result: dict[str, Any],
        expected_generation: tuple[int, int],
        expected_identity: tuple[int, int],
    ) -> bool:
        generation = list(expected_generation)
        identity_ok = "identity" not in result or result.get("identity") == list(expected_identity)
        return (
            result.get("generation") == generation
            and result.get("read_generation") == generation
            and identity_ok
        )

    def transcript_product_view(self, product_key: str, expected_identity: list[int]) -> dict[str, Any] | None:
        """Return decoded last-known-good product bytes for stale-while-revalidate, or None.

        Serves the newest complete compact facts jobd has for this file identity + shape when the
        exact byte-generation result is not yet available. Product bytes are always fully parsed
        compact items, never the raw appended line. A transport failure (`unavailable`) and a
        `pending`/`none` state both return None so the caller emits the empty pending shape.
        """
        product_meta, product_body = self.job_client.product(product_key)
        if not isinstance(product_meta, dict) or not product_meta.get("ok") or not product_body:
            return None
        if product_meta.get("state") not in {"ready", "stale"}:
            return None
        return self.decode_transcript_product(product_body, expected_identity)

    def transcript_compact_view_bounded(
        self,
        session: str,
        messages: int,
        *,
        compact_lines: int = 0,
        since: datetime | None = None,
        wait_ms: int = 0,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        """Compatibility wrapper for the retired request-thread bounded wait."""
        del wait_ms
        return self.transcript_compact_view(session, messages, compact_lines=compact_lines, since=since)

    @staticmethod
    def context_product_data(
        kind: str,
        payload: dict[str, Any],
        messages: int,
    ) -> dict[str, Any]:
        common_fields = {
            "session": str(payload.get("session") or ""),
            "path": payload.get("path"),
            "messages": messages,
            "pending": bool(payload.get("pending")),
            "stale": bool(payload.get("stale")),
            "agent": payload.get("agent"),
            "errors": copy.deepcopy(payload.get("errors") or []),
        }
        if kind == "context_tail":
            return {
                **common_fields,
                "text": "\n\n".join(payload.get("compact_lines") or []),
            }
        if kind == "context_items":
            return {
                **common_fields,
                "items": copy.deepcopy(payload.get("items") or []),
            }
        raise ValueError("unknown context product kind")

    def wait_for_jobd_operation_job(self, job_id: str, deadline_at: float) -> dict[str, Any]:
        """Wait in the bounded completion service, never in an HTTP handler."""
        poll_seconds = SESSION_FILES_OPERATION_POLL_INITIAL_SECONDS
        transient_polls = 0
        last_transient: dict[str, Any] = {}
        polling_capabilities = local_service_polling_capabilities(self.job_client)
        with deferred_transport_errors(self.job_client) as deferred_transport:
            while not self.jobd_operation_service.stop_event.is_set():
                response = self.job_client.result(job_id)
                job = response.get("job") if isinstance(response.get("job"), dict) else {}
                state = str(job.get("status") or "")
                if response.get("ok") is not True:
                    failure = dict(response)
                    if not local_service_failure_is_transient(failure, capabilities=polling_capabilities):
                        raise JobdOperationUnavailable(
                            str(failure.get("error") or "jobd result unavailable"),
                            failure,
                        )
                    transient_polls += 1
                    last_transient = failure
                elif not job:
                    raise JobdOperationUnavailable(
                        "malformed jobd result response",
                        {"error": "malformed jobd result response", "status": "malformed_result"},
                    )
                elif state == "completed":
                    return job
                elif state in {"failed", "cancelled", "superseded", "timed_out"}:
                    raise JobdOperationUnavailable(
                        str(job.get("error") or f"jobd producer {state}"),
                        dict(job),
                    )
                remaining = deadline_at - time.time()
                if remaining <= 0:
                    if deferred_transport is not None:
                        deferred_transport.publish()
                    raise JobdOperationUnavailable(
                        "jobd result deadline expired",
                        {
                            "error": "jobd result deadline expired",
                            "status": "deadline_expired",
                            "transient_polls": transient_polls,
                            "last_transient_error": str(last_transient.get("error") or ""),
                            "last_transient_transport": str(last_transient.get("_transport_error") or ""),
                        },
                        code="deadline_expired",
                        status=HTTPStatus.GATEWAY_TIMEOUT,
                    )
                self.jobd_operation_service.stop_event.wait(min(poll_seconds, remaining))
                poll_seconds = min(SESSION_FILES_OPERATION_POLL_MAX_SECONDS, poll_seconds * 2.0)
        raise JobdOperationUnavailable(
            "jobd result completion stopped",
            {"error": "jobd result completion stopped", "status": "producer_abandoned"},
            code="producer_abandoned",
        )

    def wait_for_jobd_operation_product(
        self,
        producer: JobdProductOperation,
        deadline_at: float,
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Read a potentially large accepted result through jobd's binary product frame."""
        poll_seconds = SESSION_FILES_OPERATION_POLL_INITIAL_SECONDS
        transient_polls = 0
        last_transient: dict[str, Any] = {}
        polling_capabilities = local_service_polling_capabilities(self.job_client)
        with deferred_transport_errors(self.job_client) as deferred_transport:
            while not self.jobd_operation_service.stop_event.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    raise JobdOperationUnavailable(
                        "jobd product completion cancelled",
                        {"error": "jobd product completion cancelled", "status": "producer_abandoned"},
                        code="producer_abandoned",
                    )
                metadata, body = self.job_client.product(producer.product_key)
                state = str(metadata.get("state") or "") if isinstance(metadata, dict) else ""
                if not isinstance(metadata, dict) or metadata.get("ok") is not True:
                    failure = dict(metadata) if isinstance(metadata, dict) else {"error": "jobd product unavailable"}
                    if not local_service_failure_is_transient(failure, capabilities=polling_capabilities):
                        raise JobdOperationUnavailable(
                            str(failure.get("error") or "jobd product unavailable"),
                            failure,
                        )
                    transient_polls += 1
                    last_transient = failure
                    remaining = deadline_at - time.time()
                    if remaining <= 0:
                        if deferred_transport is not None:
                            deferred_transport.publish()
                        raise JobdOperationUnavailable(
                            "jobd product deadline expired",
                            {
                                "error": "jobd product deadline expired",
                                "status": "deadline_expired",
                                "transient_polls": transient_polls,
                                "last_transient_error": str(last_transient.get("error") or ""),
                                "last_transient_transport": str(last_transient.get("_transport_error") or ""),
                            },
                            code="deadline_expired",
                            status=HTTPStatus.GATEWAY_TIMEOUT,
                        )
                    wait_event = cancel_event or self.jobd_operation_service.stop_event
                    wait_event.wait(min(poll_seconds, remaining))
                    poll_seconds = min(SESSION_FILES_OPERATION_POLL_MAX_SECONDS, poll_seconds * 2.0)
                    continue
                if body and state == "ready" and int(metadata.get("generation") or 0) == producer.generation:
                    try:
                        product = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise JobdOperationUnavailable(
                            "malformed completed jobd product",
                            {"error": str(error), "status": "malformed_product"},
                        ) from error
                    if not isinstance(product, dict):
                        raise JobdOperationUnavailable(
                            "malformed completed jobd product",
                            {"error": "malformed completed jobd product", "status": "malformed_product"},
                        )
                    return product
                if state == "none" and metadata.get("inflight") is not True:
                    response = self.job_client.result(producer.job_id)
                    job = response.get("job") if isinstance(response.get("job"), dict) else {}
                    job_state = str(job.get("status") or "")
                    if response.get("ok") is not True:
                        failure = dict(response)
                        if not local_service_failure_is_transient(failure, capabilities=polling_capabilities):
                            raise JobdOperationUnavailable(
                                str(failure.get("error") or "jobd result unavailable"),
                                failure,
                            )
                        transient_polls += 1
                        last_transient = failure
                    elif not job:
                        raise JobdOperationUnavailable(
                            "malformed jobd result response",
                            {"error": "malformed jobd result response", "status": "malformed_result"},
                        )
                    elif job_state in {"failed", "cancelled", "superseded", "timed_out"}:
                        raise JobdOperationUnavailable(
                            str(job.get("error") or f"jobd producer {job_state}"),
                            dict(job),
                        )
                remaining = deadline_at - time.time()
                if remaining <= 0:
                    if deferred_transport is not None:
                        deferred_transport.publish()
                    raise JobdOperationUnavailable(
                        "jobd product deadline expired",
                        {
                            "error": "jobd product deadline expired",
                            "status": "deadline_expired",
                            "transient_polls": transient_polls,
                            "last_transient_error": str(last_transient.get("error") or ""),
                            "last_transient_transport": str(last_transient.get("_transport_error") or ""),
                        },
                        code="deadline_expired",
                        status=HTTPStatus.GATEWAY_TIMEOUT,
                    )
                wait_event = cancel_event or self.jobd_operation_service.stop_event
                wait_event.wait(min(poll_seconds, remaining))
                poll_seconds = min(SESSION_FILES_OPERATION_POLL_MAX_SECONDS, poll_seconds * 2.0)
        raise JobdOperationUnavailable(
            "jobd product completion stopped",
            {"error": "jobd product completion stopped", "status": "producer_abandoned"},
            code="producer_abandoned",
        )

    def accept_jobd_product_operation(
        self,
        *,
        route: str,
        kind: str,
        context: dict[str, Any],
        producer: JobdProductOperation | None,
        deadline_seconds: float,
        completion: Callable[..., None],
        completion_args: tuple[Any, ...] = (),
        reservation: JobdOperationReservation | None = None,
        lane: str = "bulk",
    ) -> tuple[dict[str, Any], HTTPStatus]:
        request_id = self.new_api_request_id()
        if producer is None:
            if reservation is not None:
                reservation.release()
            return self.jobd_operation_failure_result(
                request_id,
                {"error": "jobd did not return an accepted product receipt"},
                route=route,
                operation="jobd.produce",
            ), HTTPStatus.SERVICE_UNAVAILABLE
        if reservation is None:
            reservation = self.jobd_operation_service.reserve(lane)
            if reservation is None:
                return self.jobd_operation_failure_result(
                    request_id,
                    {"error": "jobd operation completion pool is full", "status": "service_busy"},
                    route=route,
                    operation="jobd.produce",
                    code="service_busy",
                ), HTTPStatus.SERVICE_UNAVAILABLE
        deadline_at = time.time() + max(1.0, float(deadline_seconds))
        try:
            receipt = self.queued_delivery_ledger.accept_operation(
                request_id=request_id,
                route=route,
                deadline_at=deadline_at,
                progress={
                    "phase": "waiting_for_product",
                    "producer": "jobd",
                    "producer_state": "queued",
                },
                producer={
                    "service": "jobd",
                    "job_id": producer.job_id,
                    "coalesce_key": producer.product_key,
                    "generation": producer.generation,
                },
                kind=kind,
                context=context,
            )
        except Exception:
            reservation.release()
            raise
        operation_id = str(receipt["operation"]["id"])
        submitted = self.jobd_operation_service.submit_reserved(
            reservation,
            completion,
            operation_id,
            request_id,
            *completion_args,
            producer,
            deadline_at,
        )
        if submitted:
            return receipt, HTTPStatus.ACCEPTED
        result = self.jobd_operation_failure_result(
            request_id,
            {"error": "jobd operation completion worker could not start"},
            route=route,
            operation_id=operation_id,
            operation="jobd.produce",
            code="producer_failed",
        )
        self.terminalize_operation(operation_id, result, HTTPStatus.SERVICE_UNAVAILABLE)
        return result, HTTPStatus.SERVICE_UNAVAILABLE

    def complete_context_product_operation(
        self,
        operation_id: str,
        request_id: str,
        route: str,
        kind: str,
        messages: int,
        base_payload: dict[str, Any],
        producer: TranscriptProductOperation,
        deadline_at: float,
    ) -> None:
        try:
            job = self.wait_for_jobd_operation_job(producer.job_id, deadline_at)
            if not isinstance(job.get("result"), dict):
                raise JobdOperationUnavailable(
                    "malformed completed jobd product",
                    {"error": "malformed completed jobd product", "status": str(job.get("status") or "")},
                )
            product = dict(job["result"])
            if not self.cache_transcript_product_result(producer, product):
                result = self.jobd_operation_failure_result(
                    request_id,
                    {"error": "transcript changed before the accepted product completed", "status": "stale_product"},
                    route=route,
                    operation_id=operation_id,
                    operation="jobd.result",
                    code="stale_product",
                )
                self.terminalize_operation(operation_id, result, HTTPStatus.CONFLICT)
                return
            completed_payload = {
                **copy.deepcopy(base_payload),
                "compact_lines": list(product.get("compact_lines") or []),
                "items": copy.deepcopy(product.get("items") or []),
                "pending": False,
                "stale": False,
            }
            data = self.context_product_data(kind, completed_payload, messages)
            self.terminalize_operation(operation_id, self.operation_ready_result(request_id, data), HTTPStatus.OK)
        except JobdOperationUnavailable as error:
            result = self.jobd_operation_failure_result(
                request_id,
                error.failure,
                route=route,
                operation_id=operation_id,
                operation="jobd.result",
                code=error.code,
            )
            self.terminalize_operation(operation_id, result, error.status)
        except Exception as error:
            result = self.jobd_operation_failure_result(
                request_id,
                {"error": str(error), "cause": local_service_exception_cause(error)},
                route=route,
                operation_id=operation_id,
                operation="context-product.complete",
                code="producer_failed",
            )
            self.terminalize_operation(operation_id, result, HTTPStatus.INTERNAL_SERVER_ERROR)

    def accept_context_product_operation(
        self,
        *,
        route: str,
        kind: str,
        messages: int,
        payload: dict[str, Any],
        producer: TranscriptProductOperation | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self.accept_jobd_product_operation(
            route=route,
            kind=kind,
            context={"messages": messages, "session": str(payload.get("session") or "")},
            producer=producer,
            deadline_seconds=CONTEXT_OPERATION_DEADLINE_SECONDS,
            completion=self.complete_context_product_operation,
            completion_args=(route, kind, messages, payload),
        )

    def complete_filesystem_batch_operation(
        self,
        operation_id: str,
        request_id: str,
        request_ids: tuple[Any, ...],
        producer: JobdProductOperation,
        deadline_at: float,
    ) -> None:
        route = "POST /api/fs/batch"
        # Hold the jobd interaction lease across the whole product-poll window.  Under a saturated
        # gate this poll thread can be starved past the broker's idle window; the held lease vetoes
        # its idle shutdown so the socket cannot vanish out from under this operation (W15 #4).
        self.jobd_fs_batch_lease.acquire()
        try:
            product = self.wait_for_jobd_operation_product(producer, deadline_at)
            if not isinstance(product.get("responses"), list):
                raise JobdOperationUnavailable(
                    "malformed completed filesystem batch product",
                    {"error": "malformed completed filesystem batch product", "status": "malformed_product"},
                )
            data = self.materialize_filesystem_batch_product(product, request_ids)
            self.terminalize_operation(operation_id, self.operation_ready_result(request_id, data), HTTPStatus.OK)
        except JobdOperationUnavailable as error:
            result = self.jobd_operation_failure_result(
                request_id,
                error.failure,
                route=route,
                operation_id=operation_id,
                operation="jobd.result",
                code=error.code,
            )
            self.terminalize_operation(operation_id, result, error.status)
        except Exception as error:
            result = self.jobd_operation_failure_result(
                request_id,
                {"error": str(error), "cause": local_service_exception_cause(error)},
                route=route,
                operation_id=operation_id,
                operation="filesystem-batch.complete",
                code="producer_failed",
            )
            self.terminalize_operation(operation_id, result, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            self.jobd_fs_batch_lease.release()

    @staticmethod
    def materialize_filesystem_batch_product(
        product: dict[str, Any],
        request_ids: tuple[Any, ...] | list[Any],
    ) -> dict[str, Any]:
        responses = product.get("responses")
        if not isinstance(responses, list):
            raise JobdOperationUnavailable(
                "malformed completed filesystem batch product",
                {"error": "malformed completed filesystem batch product", "status": "malformed_product"},
            )
        responses_by_id = {
            response.get("id"): response
            for response in responses
            if isinstance(response, dict) and not isinstance(response.get("id"), bool)
        }
        materialized = []
        for index, caller_id in enumerate(request_ids):
            response = copy.deepcopy(responses_by_id.get(index) or {
                "ok": False,
                "status": int(HTTPStatus.SERVICE_UNAVAILABLE),
                "error": "filesystem batch result missing",
            })
            response["id"] = caller_id
            materialized.append(response)
        return {**copy.deepcopy(product), "responses": materialized}

    def fs_batch_invalid_request_result(
        self,
        payload: dict[str, Any],
        error: ValueError,
    ) -> dict[str, Any]:
        """Build the one typed rejection for a malformed Finder batch.

        ``filesystem.validated_batch_requests`` stays the only owner of what is acceptable; this
        is the only owner of how that rejection reaches the browser, so the HTTP handler and the
        app payload cannot disagree about the message, the code, or the causal frame.
        """
        requests = payload.get("requests", [])
        if isinstance(requests, list):
            message_key = "request.error.tooManyItems"
            message_params = {"field": "requests", "max": filesystem.MAX_BATCH_REQUESTS}
            details = {"requests": len(requests), "maximum": filesystem.MAX_BATCH_REQUESTS}
        else:
            message_key = "request.error.list"
            message_params = {"field": "requests"}
            details = {"requests_type": type(requests).__name__}
        return common.error_payload(
            str(error),
            message_key=message_key,
            message_params=message_params,
            canonical=True,
            code="invalid_request",
            origin="server.http",
            retryable=False,
            details=details,
            stack=[{
                "component": "server.http",
                "operation": "POST /api/fs/batch",
                "code": "invalid_request",
            }],
            request_id=self.new_api_request_id(),
        )

    def fs_batch_http_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], HTTPStatus]:
        """Submit one bounded Finder product and return its durable receipt."""
        try:
            requests = filesystem.validated_batch_requests(payload)
        except ValueError as error:
            return self.fs_batch_invalid_request_result(payload, error), HTTPStatus.BAD_REQUEST
        reservation = self.jobd_operation_service.reserve("bulk")
        if reservation is None:
            request_id = self.new_api_request_id()
            result = self.jobd_operation_failure_result(
                request_id,
                {"error": "jobd operation completion pool is full", "status": "service_busy"},
                route="POST /api/fs/batch",
                operation="jobd.produce",
                code="service_busy",
            )
            self.record_operation_failure("", result)
            return result, HTTPStatus.SERVICE_UNAVAILABLE
        job_payload, product_key, request_ids = filesystem_batch_submission(
            payload,
            key_prefix="fs-batch",
        )
        generation = 1
        # Hold the jobd interaction lease while contacting the broker so it cannot idle-shut its
        # socket during this exchange (W15 #4).  The accepted-operation completion worker below
        # re-holds its own lease across the long product-poll window between the two /api/fs/batch
        # calls; this acquire also spawns the broker if it had idled out before this request.
        self.jobd_fs_batch_lease.acquire()
        try:
            response, body = self.job_client.produce(
                "filesystem_batch",
                job_payload,
                priority="interactive",
                generation=generation,
                coalesce_key=product_key,
                deadline_ms=int(FS_BATCH_OPERATION_DEADLINE_SECONDS * 1000),
                # jobd atomically checks its product store without waiting in the
                # serial RPC handler. Warm products return here; cold work keeps
                # using the accepted-operation SSE path below.
                delivery="ready_or_receipt",
            )
        except Exception:
            reservation.release()
            raise
        finally:
            self.jobd_fs_batch_lease.release()
        if body and response.get("ok") is True:
            reservation.release()
            try:
                product = self.decode_filesystem_watch_batch_product(body)
                return self.materialize_filesystem_batch_product(product, request_ids), HTTPStatus.OK
            except JobdOperationUnavailable as error:
                result = self.jobd_operation_failure_result(
                    self.new_api_request_id(),
                    error.failure,
                    route="POST /api/fs/batch",
                    operation="jobd.product",
                    code=error.code,
                )
                self.record_operation_failure("", result)
                return result, error.status
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        job_id = str(job.get("job_id") or "")
        producer_state = str(job.get("status") or "")
        if body or response.get("ok") is not True or not job_id or producer_state not in {"queued", "running", "completed"}:
            reservation.release()
            failure = dict(response)
            if body:
                failure["error"] = "filesystem batch returned unusable product bytes"
            elif not failure.get("error"):
                failure["error"] = "jobd did not return an accepted filesystem batch receipt"
            result = self.jobd_operation_failure_result(
                self.new_api_request_id(),
                failure,
                route="POST /api/fs/batch",
                operation="jobd.produce",
            )
            self.record_operation_failure("", result)
            return result, HTTPStatus.SERVICE_UNAVAILABLE
        producer = JobdProductOperation(job_id=job_id, product_key=product_key, generation=generation)
        return self.accept_jobd_product_operation(
            route="POST /api/fs/batch",
            kind="fs_batch",
            context={
                "session": "",
                "product_key": product_key,
                "request_ids": request_ids,
            },
            producer=producer,
            deadline_seconds=FS_BATCH_OPERATION_DEADLINE_SECONDS,
            completion=self.complete_filesystem_batch_operation,
            completion_args=(tuple(request_ids),),
            reservation=reservation,
            lane="bulk",
        )

    def wait_for_filesystem_operation_product(
        self,
        producer: JobdProductOperation,
        deadline_at: float,
    ) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
        """Read one retained filesystem product without interpreting opaque bytes."""
        poll_seconds = SESSION_FILES_OPERATION_POLL_INITIAL_SECONDS
        transient_polls = 0
        last_transient: dict[str, Any] = {}
        while not self.jobd_operation_service.stop_event.is_set():
            metadata, body = self.job_client.product(producer.product_key)
            state = str(metadata.get("state") or "") if isinstance(metadata, dict) else ""
            if not isinstance(metadata, dict) or metadata.get("ok") is not True:
                failure = dict(metadata) if isinstance(metadata, dict) else {"error": "jobd product unavailable"}
                # A transient transport blip on one poll is not the producer failing.  Treating the
                # first non-OK product read as terminal turned a recoverable RPC timeout into a
                # failed editor open even though the operation still had most of its 120s budget
                # and the worker went on to produce the bytes.  Genuine producer terminal states
                # are handled below and still fail immediately.
                if not local_service_failure_is_transient(failure):
                    raise JobdOperationUnavailable(
                        str(failure.get("error") or "jobd product unavailable"),
                        failure,
                    )
                transient_polls += 1
                last_transient = failure
                remaining = deadline_at - time.time()
                if remaining <= 0:
                    raise JobdOperationUnavailable(
                        "jobd product deadline expired",
                        {
                            "error": "jobd product deadline expired",
                            "status": "deadline_expired",
                            "transient_polls": transient_polls,
                            "last_transient_error": str(last_transient.get("error") or ""),
                            "last_transient_transport": str(last_transient.get("_transport_error") or ""),
                        },
                        code="deadline_expired",
                        status=HTTPStatus.GATEWAY_TIMEOUT,
                    )
                self.jobd_operation_service.stop_event.wait(min(poll_seconds, remaining))
                poll_seconds = min(SESSION_FILES_OPERATION_POLL_MAX_SECONDS, poll_seconds * 2.0)
                continue
            product = metadata.get("product") if isinstance(metadata.get("product"), dict) else None
            if (body or metadata.get("artifact") is True) and state == "ready" and int(metadata.get("generation") or 0) == producer.generation and product is not None:
                schedule = dict(metadata.get("schedule") or {}) if isinstance(metadata.get("schedule"), dict) else {}
                if metadata.get("artifact") is True:
                    schedule["artifact"] = True
                if transient_polls:
                    schedule["transient_polls"] = transient_polls
                return dict(product), body, schedule
            if state == "none" and metadata.get("inflight") is not True:
                response = self.job_client.result(producer.job_id)
                job = response.get("job") if isinstance(response.get("job"), dict) else {}
                job_state = str(job.get("status") or "")
                if response.get("ok") is not True or job_state in {"failed", "cancelled", "superseded", "timed_out"}:
                    failure = dict(job) if job else dict(response)
                    typed_failure = self.typed_filesystem_operation_failure(failure)
                    if typed_failure is not None:
                        filesystem_error, status = typed_failure
                        raise JobdOperationUnavailable(
                            str(filesystem_error.get("error") or "filesystem operation failed"),
                            {"filesystem_error": filesystem_error, "status": int(status)},
                            code=str(filesystem_error.get("user_message", {}).get("key") or "filesystem_error"),
                            status=status,
                        )
                    raise JobdOperationUnavailable(
                        str(failure.get("error") or f"jobd producer {job_state or 'unavailable'}"),
                        failure,
                    )
            remaining = deadline_at - time.time()
            if remaining <= 0:
                raise JobdOperationUnavailable(
                    "jobd product deadline expired",
                    {
                        "error": "jobd product deadline expired",
                        "status": "deadline_expired",
                        "transient_polls": transient_polls,
                    },
                    code="deadline_expired",
                    status=HTTPStatus.GATEWAY_TIMEOUT,
                )
            self.jobd_operation_service.stop_event.wait(min(poll_seconds, remaining))
            poll_seconds = min(SESSION_FILES_OPERATION_POLL_MAX_SECONDS, poll_seconds * 2.0)
        raise JobdOperationUnavailable(
            "jobd product completion stopped",
            {"error": "jobd product completion stopped", "status": "producer_abandoned"},
            code="producer_abandoned",
        )

    def complete_filesystem_operation(
        self,
        operation_id: str,
        request_id: str,
        route: str,
        reload_yolo_rules: bool,
        producer: JobdProductOperation,
        deadline_at: float,
    ) -> None:
        try:
            product, body, schedule = self.wait_for_filesystem_operation_product(producer, deadline_at)
            self.queued_delivery_ledger.record_operation_schedule(operation_id, schedule)
            if product.get("format") != "json":
                raise JobdOperationUnavailable(
                    "filesystem operation returned an unsupported product format",
                    {"error": "filesystem operation returned an unsupported product format", "status": "malformed_product"},
                )
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise JobdOperationUnavailable(
                    "malformed completed filesystem product",
                    {"error": "malformed completed filesystem product", "status": "malformed_product"},
                )
            if reload_yolo_rules:
                data["yolo_rules"] = yolo_rules.reload_rules()
            self.terminalize_operation(operation_id, self.operation_ready_result(request_id, data), HTTPStatus.OK)
        except JobdOperationUnavailable as error:
            typed_failure = self.typed_filesystem_operation_failure(error.failure)
            if typed_failure is not None:
                filesystem_error, status = typed_failure
                result = self.typed_filesystem_operation_failed_result(
                    request_id,
                    filesystem_error,
                    status,
                    route=route,
                    operation_id=operation_id,
                )
            else:
                result = self.jobd_operation_failure_result(
                    request_id,
                    error.failure,
                    route=route,
                    operation_id=operation_id,
                    operation="jobd.result",
                    code=error.code,
                )
            self.terminalize_operation(operation_id, result, error.status)
        except Exception as error:
            result = self.jobd_operation_failure_result(
                request_id,
                {"error": str(error), "cause": local_service_exception_cause(error)},
                route=route,
                operation_id=operation_id,
                operation="filesystem-operation.complete",
                code="producer_failed",
            )
            self.terminalize_operation(operation_id, result, HTTPStatus.INTERNAL_SERVER_ERROR)

    def filesystem_operation_product_generation(self) -> str:
        """Return a watchd revision only when it can invalidate retained filesystem reads."""
        with self.client_watch_service.lock:
            record = self.client_watch_service.event_watcher_record
            if record is None or not record.filesystem_healthy or not record.watchd_epoch:
                return ""
            return f"watchd:{record.watchd_epoch}:{record.watchd_revision}"

    def filesystem_operation_http_payload(
        self,
        *,
        route: str,
        operation: str,
        path: str,
        args: dict[str, Any] | None = None,
        reload_yolo_rules: bool = False,
        scope: str = "local",
    ) -> FilesystemOperationHttpResponse:
        """Submit one filesystem descriptor and persist a cold-operation receipt."""
        operation_args = dict(args or {})
        refusal = self.refused_filesystem_operation_request(operation, path, operation_args)
        if refusal is not None:
            return FilesystemOperationHttpResponse(*refusal)
        request_id = self.new_api_request_id()
        # Priority (and therefore the completion lane) is computed BEFORE admission: a point read
        # reserves the point lane and a bounded mutation the mutation lane, so neither can be
        # refused or stranded because bulk completion polls hold the shared pool.
        priority = filesystem_operation_priority(operation)
        reservation = self.jobd_operation_service.reserve(jobd_operation_lane(priority))
        if reservation is None:
            result = self.jobd_operation_failure_result(
                request_id,
                {"error": "jobd operation completion pool is full", "status": "service_busy"},
                route=route,
                operation="jobd.produce",
                code="service_busy",
            )
            self.record_operation_failure("", result)
            return FilesystemOperationHttpResponse(result, HTTPStatus.SERVICE_UNAVAILABLE)
        generation = self.filesystem_operation_product_generation()
        uncoalesced_reason = ""
        # A watchd generation is authoritative: its revision advances on any observed change, so a
        # completed product carrying that generation is safe to reuse.  A stat identity is not --
        # `st_mtime_ns` is only as fine as the filesystem's timestamp tick, so two writes inside one
        # tick that keep the same size produce the same key for different bytes.  Such a submission
        # may still join in-flight work (which has produced nothing yet and so cannot be stale), but
        # it must never accept an already-stored product.
        fresh_only = False
        if not generation and priority == "point":
            # watchd cannot invalidate retained reads right now, and a random key would make every
            # concurrent open of the same file its own job in a lane bounded at two.  The file's own
            # content identity coalesces the concurrent duplicates without pinning a stale product.
            generation, uncoalesced_reason = filesystem_point_content_generation(path)
            fresh_only = bool(generation)
        if operation in FILESYSTEM_RETAINED_READ_OPERATIONS and generation:
            job_payload, product_key = filesystem_operation_submission(
                operation,
                path,
                operation_args,
                scope=scope,
                generation=generation,
            )
        else:
            job_payload = filesystem_operation_descriptor(operation, path, operation_args)
            product_key = f"filesystem-operation:{uuid.uuid4().hex}"
            if priority == "point" and not uncoalesced_reason:
                uncoalesced_reason = "operation_not_retained"
        generation = 1
        try:
            response, body = self.job_client.produce(
                "filesystem_operation",
                job_payload,
                priority=priority,
                generation=generation,
                coalesce_key=product_key,
                deadline_ms=int(FS_BATCH_OPERATION_DEADLINE_SECONDS * 1000),
                delivery="ready_or_receipt",
                fresh_only=fresh_only,
            )
        except Exception:
            reservation.release()
            raise
        if response.get("_transport_error") == "timeout":
            response, body = self.job_client.produce(
                "filesystem_operation", job_payload, priority=priority, generation=generation,
                coalesce_key=product_key, deadline_ms=int(FS_BATCH_OPERATION_DEADLINE_SECONDS * 1000), delivery="receipt",
                fresh_only=fresh_only,
            )
        if body and response.get("ok") is True:
            product = response.get("product") if isinstance(response.get("product"), dict) else None
            if response.get("state") == "ready" and product is not None and product.get("format") == "json":
                reservation.release()
                return FilesystemOperationHttpResponse(None, HTTPStatus.OK, body=body, product=dict(product))
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        job_id = str(job.get("job_id") or "")
        producer_state = str(job.get("status") or "")
        if body or response.get("ok") is not True or not job_id or producer_state not in {"queued", "running", "completed"}:
            reservation.release()
            typed_failure = self.typed_filesystem_operation_failure(response)
            if typed_failure is not None:
                filesystem_error, status = typed_failure
                return FilesystemOperationHttpResponse(filesystem_error, status)
            failure = dict(response)
            failure.setdefault("error", "jobd did not return an accepted filesystem operation receipt")
            result = self.jobd_operation_failure_result(request_id, failure, route=route, operation="jobd.produce")
            self.record_operation_failure("", result)
            return FilesystemOperationHttpResponse(result, HTTPStatus.SERVICE_UNAVAILABLE)
        producer = JobdProductOperation(job_id=job_id, product_key=product_key, generation=generation)
        payload, status = self.accept_jobd_product_operation(
            route=route,
            kind="filesystem_operation",
            # `uncoalesced` names why a point read had to take a non-coalescing key, so a lane full
            # of duplicate reads of one file is attributable instead of merely visible.
            context={"operation": operation, "path": path, "product_key": product_key, "uncoalesced": uncoalesced_reason},
            producer=producer,
            deadline_seconds=FS_BATCH_OPERATION_DEADLINE_SECONDS,
            completion=self.complete_filesystem_operation,
            completion_args=(route, reload_yolo_rules),
            reservation=reservation,
            lane=jobd_operation_lane(priority),
        )
        return FilesystemOperationHttpResponse(payload, status)

    def filesystem_operation_relay(
        self,
        *,
        route: str,
        operation: str,
        path: str,
        args: dict[str, Any] | None = None,
    ) -> FilesystemOperationHttpResponse:
        """Relay one browser-consumed byte product without a receipt protocol.

        There is no browser receipt to fall back on for a raw/download/preview/zip byte stream, so
        this response is synchronous.  It no longer BLOCKS a serial jobd handler slot for the whole
        job: it submits with a zero-wait ``produce`` (warm bytes return immediately) and, on a cold
        receipt, waits for the product on the ONE shared filesystem product-poll owner
        (``wait_for_filesystem_operation_product``) rather than inside the daemon.  The former
        ``relay`` action held one of jobd's bounded concurrent-handler slots for the entire job, so
        enough concurrent downloads refused every other client with ``service busy``.
        """
        request_id = self.new_api_request_id()
        descriptor = filesystem_operation_descriptor(operation, path, dict(args or {}))
        product_key = f"filesystem-operation-relay:{uuid.uuid4().hex}"
        priority = filesystem_operation_priority(operation)
        deadline_ms = int(FS_BATCH_OPERATION_DEADLINE_SECONDS * 1000)
        response, body = self.job_client.produce(
            "filesystem_operation",
            descriptor,
            priority=priority,
            generation=1,
            coalesce_key=product_key,
            deadline_ms=deadline_ms,
            delivery="ready_or_receipt",
        )
        product = response.get("product") if isinstance(response.get("product"), dict) else None
        if response.get("ok") is True and body and response.get("state") in {"ready", "stale"} and product is not None:
            return FilesystemOperationHttpResponse(None, HTTPStatus.OK, body=body, product=dict(product))
        if response.get("ok") is True and response.get("state") in {"ready", "stale"} and product is not None and response.get("artifact") is True:
            try:
                return filesystem_artifact_http_response(self.job_client, product_key, 1, product)
            except JobdOperationUnavailable as error:
                result = self.jobd_operation_failure_result(request_id, error.failure, route=route, operation="jobd.artifact_open", code=error.code)
                self.record_operation_failure("", result)
                return FilesystemOperationHttpResponse(result, error.status)
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        job_id = str(job.get("job_id") or "")
        producer_state = str(job.get("status") or "")
        if body or response.get("ok") is not True or not job_id or producer_state not in {"queued", "running", "completed"}:
            typed_failure = self.typed_filesystem_operation_failure(response)
            if typed_failure is not None:
                filesystem_error, status = typed_failure
                return FilesystemOperationHttpResponse(filesystem_error, status)
            failure = dict(response)
            failure.setdefault("error", "jobd did not return an accepted filesystem operation receipt")
            result = self.jobd_operation_failure_result(request_id, failure, route=route, operation="jobd.produce")
            self.record_operation_failure("", result)
            return FilesystemOperationHttpResponse(result, HTTPStatus.SERVICE_UNAVAILABLE)
        producer = JobdProductOperation(job_id=job_id, product_key=product_key, generation=1)
        try:
            product_meta, product_body, schedule = self.wait_for_filesystem_operation_product(
                producer,
                time.time() + FS_BATCH_OPERATION_DEADLINE_SECONDS,
            )
        except JobdOperationUnavailable as error:
            typed_failure = self.typed_filesystem_operation_failure(error.failure)
            if typed_failure is not None:
                filesystem_error, status = typed_failure
                return FilesystemOperationHttpResponse(filesystem_error, status)
            result = self.jobd_operation_failure_result(
                request_id,
                error.failure,
                route=route,
                operation="jobd.product",
                code=error.code,
            )
            self.record_operation_failure("", result)
            return FilesystemOperationHttpResponse(result, error.status)
        if not product_body and schedule.get("artifact") is True:
            try:
                return filesystem_artifact_http_response(self.job_client, product_key, 1, product_meta)
            except JobdOperationUnavailable as error:
                result = self.jobd_operation_failure_result(request_id, error.failure, route=route, operation="jobd.artifact_open", code=error.code)
                self.record_operation_failure("", result)
                return FilesystemOperationHttpResponse(result, error.status)
        return FilesystemOperationHttpResponse(None, HTTPStatus.OK, body=product_body, product=dict(product_meta))

    def context_tail(
        self,
        session: str,
        messages: int,
        *,
        accept_pending: bool = True,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        safe_messages = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        payload, status, producer = self.transcript_compact_view_result(session, safe_messages, compact_lines=safe_messages)
        if status != HTTPStatus.OK:
            return payload, status
        if payload.get("pending") and accept_pending:
            return self.accept_context_product_operation(
                route="GET /api/context",
                kind="context_tail",
                messages=safe_messages,
                payload=payload,
                producer=producer,
            )
        return self.context_product_data("context_tail", payload, safe_messages), HTTPStatus.OK

    def context_items(
        self,
        session: str,
        messages: int,
        *,
        accept_pending: bool = True,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        safe_messages = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        payload, status, producer = self.transcript_compact_view_result(session, safe_messages)
        if status != HTTPStatus.OK:
            return payload, status
        if payload.get("pending") and accept_pending:
            return self.accept_context_product_operation(
                route="GET /api/context-items",
                kind="context_items",
                messages=safe_messages,
                payload=payload,
                producer=producer,
            )
        return self.context_product_data("context_items", payload, safe_messages), HTTPStatus.OK

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
            diagnostic = cmd_error(result, "tmux select-window failed")
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
        # Session retirement must stop an existing approval worker, but asking whether one exists
        # is not demand to launch approvald for a session that never enabled YOLO.
        workers = approval_client.status_session_if_running(session)
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
        try:
            retirement_identity = capture_tmux_session_retirement(session)
        except SessionRetirementError as error:
            diagnostic = str(error)
            return {
                "session": session,
                "killed": False,
                **user_message_payload("status.sessionKillFailed", diagnostic, error=diagnostic),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        result = tmux(["kill-session", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = cmd_error(result, "tmux kill-session failed")
            return {
                "session": session,
                **user_message_payload("status.sessionKillFailed", error, error=error),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        try:
            join_tmux_session_retirement(retirement_identity)
        except SessionRetirementError as error:
            diagnostic = str(error)
            return {
                "session": session,
                "killed": False,
                **user_message_payload("status.sessionKillFailed", diagnostic, error=diagnostic),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
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

    def create_next_session_plan(self) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if len(self.sessions) >= MAX_YOLOMUX_SESSION_TABS:
            diagnostic = f"maximum session tabs reached: {MAX_YOLOMUX_SESSION_TABS}"
            return {"sessions": self.sessions, **user_message_payload("session.error.maximumTabs", diagnostic, limit=MAX_YOLOMUX_SESSION_TABS)}, HTTPStatus.CONFLICT
        session = next_numbered_session_name(self.sessions)
        if session is None:
            diagnostic = f"no available numbered session names from 1 to {MAX_YOLOMUX_SESSION_TABS}"
            return {"sessions": self.sessions, **user_message_payload("session.error.noAvailableNumberedNames", diagnostic, limit=MAX_YOLOMUX_SESSION_TABS)}, HTTPStatus.CONFLICT
        with self.session_reservation_lock:
            generation = max(time.time_ns() // 1_000_000, self.session_reservation_generation + 1)
            if generation > JAVASCRIPT_MAX_SAFE_INTEGER:
                raise RuntimeError("create-session generation exceeds the JavaScript safe integer range")
            self.session_reservation_generation = generation
        return {"ok": True, "session": session, "generation": generation}, HTTPStatus.OK

    def create_next_session(
        self,
        agent: str,
        dangerously_yolo: bool | None = None,
        terminal: str | None = None,
        requested_session: str | None = None,
        reservation_generation: int | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
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
        requested = str(requested_session or "").strip()
        if requested and (requested != session or not isinstance(reservation_generation, int) or reservation_generation <= 0):
            diagnostic = "create-session lifecycle reservation no longer matches the next available name"
            return {"sessions": self.sessions, **user_message_payload("status.sessionCreateFailedDefault", diagnostic, error=diagnostic)}, HTTPStatus.CONFLICT
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
            "generation": int(reservation_generation or 0),
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
        # Item 6: an upload is a successful YOLOmux file create -- route it into the ONE hot-path
        # index owner (the same path write/delete/rename take) so an uploaded file is searchable in
        # seconds. Covers both browser uploads and editor uploads, which share this save funnel.
        if saved:
            filesystem.reindex_roots_for_paths([item["path"] for item in saved], reason="fs-upload")
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

        if not capture_pane:
            # Roster path: derive working/idle from the LIVE pane via a cheap visible-only capture
            # plus cheap prompt presence from the already-captured text. This avoids the expensive
            # hybrid transcript / bash double-capture fan-out while still lighting roster approval badges.
            classification = self.roster_pane_classification(
                session,
                target,
                discovered_sessions=discovered_sessions,
            )
            return dict(classification["prompt"]), dict(classification["screen"])
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
            screen_classifier=self.agent_pane_screen_classification,
            discover_sessions_func=discover_sessions,
        )
        return normalized_prompt_state(state.prompt), dict(state.screen)

    @staticmethod
    def agent_pane_screen_classification(visible_text: str, pane_target: str | None) -> dict[str, Any]:
        return dict(agent_screen_state(visible_text, pane_target=pane_target))

    def roster_pane_classification(
        self,
        session: str,
        target: str,
        *,
        discovered_sessions: dict[str, SessionInfo],
    ) -> dict[str, dict[str, Any]]:
        def roster_prompt_classifier(
            _prompt_target: str,
            visible_text: str,
            pane_text: str | None,
            _prompt_source: str,
        ) -> dict[str, Any]:
            if pane_text is None:
                return approval_prompt_state(visible_text)
            return approval_prompt_state(visible_text, pane_text)

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
            screen_classifier=self.agent_pane_screen_classification,
        )
        if state.reason_code in {"disconnected", "error"}:
            return {
                "prompt": normalized_prompt_state(),
                "screen": {"key": "idle", "text": ""},
            }
        return {
            "prompt": normalized_prompt_state(state.prompt),
            "screen": dict(state.screen),
        }

    def agent_window_screen_state(
        self,
        agent: AgentInfo,
        preclassified_by_target: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target = str(agent.pane_target or "")
        if target and preclassified_by_target and target in preclassified_by_target:
            preclassified = preclassified_by_target[target]
            screen = preclassified.get("screen") if isinstance(preclassified, dict) else None
            return dict(screen if isinstance(screen, dict) else preclassified)
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
        if key == "blocked":
            return "blocked"
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
            local_key = str(key)
            prefix = self.host_identity.qualify_key("activity", "")
            if local_key.startswith(prefix):
                local_key = local_key.removeprefix(prefix)
            record = dict(value) if isinstance(value, dict) else {}
            record["active_recency_ts"] = self.activity_record_recency_ts(record)
            result[local_key] = record
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

    def prune_absent_agent_window_transition_state(self, discovered_sessions: dict[str, SessionInfo]) -> None:
        """Retire dead pane identities after a complete status discovery for each session."""
        live: set[str] = set()
        covered_sessions = set(discovered_sessions)
        for session, info in discovered_sessions.items():
            for agent in info.agents:
                kind = str(agent.kind or "").lower()
                if kind not in {"claude", "codex"} or not agent.pane_target:
                    continue
                window, _pane = session_files.agent_window_for_info(info, agent)
                live.add("\x1f".join((session, window, str(agent.pane_target), kind)))
        with self.agent_window_transition_lock:
            self.agent_window_transition_state = {
                key: state
                for key, state in self.agent_window_transition_state.items()
                if key in live or key.split("\x1f", 1)[0] not in covered_sessions
            }

    @staticmethod
    def attention_ack_key(*parts: Any, host_identity: Any | None = None) -> str:
        identity = host_identity or current_host_identity()
        value = json.dumps([str(part or "") for part in parts], separators=(",", ":"))
        return identity.qualify_key("attention-ack", value)

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
        return self.attention_ack_key("prompt", session, signature, host_identity=self.host_identity) if signature else ""

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
        with file_lock(self.tmux_ai_status_path, dir_mode=0o700):
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
        with file_lock(self.tmux_ai_status_path, dir_mode=0o700):
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
        return self.attention_ack_key("agent-window", session, self.agent_window_index_key(window), pane_target, kind, state, signature, host_identity=self.host_identity)

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
        # statusd owns the retained status bytes; the web process only tells the daemon to
        # rebuild on the next snapshot. There is no in-web status cache to clear anymore.
        self.status_client.invalidate("auto_approve")

    def _read_shared_attention_acks_locked(self) -> tuple[dict[str, float], int]:
        data = self._read_shared_tmux_ai_status_locked()
        attention = data.get("attention_acks") if isinstance(data.get("attention_acks"), dict) else {}
        keys = self._normalized_attention_ack_keys(attention)
        try:
            rev = int(attention.get("rev", 0)) if isinstance(attention, dict) else 0
        except (TypeError, ValueError):
            rev = 0
        return keys, max(0, rev)

    def _normalized_attention_ack_keys(self, payload: object) -> dict[str, float]:
        """Return valid acknowledgement timestamps from either durable format."""

        raw_keys = payload.get("keys") if isinstance(payload, dict) and isinstance(payload.get("keys"), dict) else {}
        keys: dict[str, float] = {}
        for raw_key, raw_ts in raw_keys.items():
            key = str(raw_key or "").strip()
            try:
                ts = float(raw_ts)
            except (TypeError, ValueError):
                continue
            if key and ts > 0:
                keys[key] = ts
        return keys

    def _read_legacy_attention_acks_locked(self) -> tuple[dict[str, float], int]:
        """Read pre-status-file acknowledgements until their contents are durably migrated."""

        data = read_json_file(common.LEGACY_ATTENTION_ACKS_PATH, {}, exceptions=(OSError, json.JSONDecodeError, TypeError, ValueError))
        keys = self._normalized_attention_ack_keys(data)
        try:
            rev = int(data.get("rev", 0)) if isinstance(data, dict) else 0
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
        with file_lock(self.tmux_ai_status_path, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            attention = status.get("attention_acks") if isinstance(status.get("attention_acks"), dict) else {}
            merged, rev = self._read_shared_attention_acks_locked()
            legacy_keys, legacy_rev = self._read_legacy_attention_acks_locked()
            rev = max(rev, legacy_rev)
            self._prune_attention_ack_dict(merged, now)
            before_keys = set(merged)
            for key, ts in legacy_keys.items():
                if key not in merged:
                    merged[key] = ts
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
        with file_lock(self.tmux_ai_status_path, dir_mode=0o700):
            status = self._read_shared_tmux_ai_status_locked()
            attention = status.get("attention_acks") if isinstance(status.get("attention_acks"), dict) else {}
            file_keys, rev = self._read_shared_attention_acks_locked()
            legacy_keys, legacy_rev = self._read_legacy_attention_acks_locked()
            rev = max(rev, legacy_rev)
            now = time.time()
            self._prune_attention_ack_dict(file_keys, now)
            before_keys = set(file_keys)
            for key, ts in legacy_keys.items():
                if key not in file_keys:
                    file_keys[key] = ts
            self._prune_attention_ack_dict(file_keys, now)
            if set(file_keys) != before_keys:
                rev += 1
                status["attention_acks"] = {
                    "rev": rev,
                    "updated_at": now,
                    "keys": file_keys,
                    "writer": self.background_owner.owner_payload(),
                    **({"legacy_rev": attention.get("legacy_rev")} if isinstance(attention, dict) and attention.get("legacy_rev") else {}),
                }
                self._write_shared_tmux_ai_status_locked(status)
            with self.client_watch_service.lock:
                if rev <= self.client_watch_service.attention_ack_rev:
                    return False
                self.client_watch_service.attention_ack_rev = rev
            with self.attention_ack_lock:
                changed = set(self.attention_ack_keys) != set(file_keys)
                self.attention_ack_keys = dict(file_keys)
        return changed

    def refresh_shared_attention_acks(self, *, trigger: str, notify_followers: bool = False) -> list[str]:
        with self.attention_ack_lock:
            previous_keys = set(self.attention_ack_keys)
        if not self.merge_shared_attention_acks():
            return []
        self.invalidate_auto_approve_cache()
        with self.attention_ack_lock:
            acknowledged = sorted(set(self.attention_ack_keys) - previous_keys)
            acknowledged_at = {key: self.attention_ack_keys[key] for key in acknowledged}
        payload = {"acknowledged": acknowledged, "acknowledged_at": acknowledged_at}
        if notify_followers:
            self.notify_background_client_event_followers(
                "attention_acks_changed",
                payload,
                self.shared_background_client_event_record("attention_acks_changed", payload),
            )
        self.publish_client_event(
            "attention_acks_changed",
            payload,
            trigger=trigger,
            cache="ready",
        )
        return ["attention_acks_changed"]

    def poll_attention_acks_client_event_once(self) -> list[str]:
        return self.refresh_shared_attention_acks(trigger="timer")

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
            cache[root] = self.cached_agent_window_git_inventory(root)
        git_data = cache[root]
        if not isinstance(git_data, dict):
            return None
        return copy.deepcopy(git_data)

    def cached_agent_window_git_inventory(self, root: str) -> dict[str, Any] | None:
        # Reuse the session-files watcher generation: when the fs watcher covers the repo, its dirty
        # generation is authoritative, so an unchanged generation lets a warm refresh skip the git
        # spawn. A short time backstop bounds staleness if the watcher misses a change; an uncovered
        # repo always re-spawns (never cached), preserving today's always-fresh behavior there.
        covers = False
        try:
            covers = self.watcher_covers_repo(Path(root))
        except (OSError, ValueError):
            covers = False
        generation = self.repo_dirty_generation(root) if covers else None
        now = time.monotonic()
        if generation is not None:
            with self.agent_window_git_inventory_cache_lock:
                cached = self.agent_window_git_inventory_cache.get(root)
                if cached is not None and cached[0] == generation and now - cached[1] <= AGENT_WINDOW_GIT_INVENTORY_MAX_AGE_SECONDS:
                    return cached[2]
        git_data = git_inventory(root)
        if generation is not None:
            with self.agent_window_git_inventory_cache_lock:
                self.agent_window_git_inventory_cache[root] = (generation, now, git_data)
                if len(self.agent_window_git_inventory_cache) > AGENT_WINDOW_GIT_INVENTORY_CACHE_MAX:
                    oldest = min(self.agent_window_git_inventory_cache, key=lambda key: self.agent_window_git_inventory_cache[key][1])
                    self.agent_window_git_inventory_cache.pop(oldest, None)
        return git_data

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
        owned_rows_by_target: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
        snapshot_revision: int = 0,
    ) -> list[dict[str, Any]]:
        gathered_agents = self.agent_window_gathered_agents(
            session,
            info=info,
            discovered_sessions=discovered_sessions,
            activity_snapshot=activity_snapshot,
            preclassified_by_target=preclassified_by_target,
            files_payload=files_payload,
            include_path_metadata=include_path_metadata,
            owned_rows_by_target=owned_rows_by_target,
        )
        return assemble_agent_window_rows(gathered_agents, snapshot_revision=snapshot_revision)

    def agent_window_gathered_agents(
        self,
        session: str,
        *,
        info: SessionInfo | None = None,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        activity_snapshot: dict[str, Any] | None = None,
        preclassified_by_target: dict[str, dict[str, Any]] | None = None,
        files_payload: dict[str, Any] | None = None,
        include_path_metadata: bool = True,
        owned_rows_by_target: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Impure per-agent gathering: tmux screen state, attention/cooldown, path/git.

        Returns the same JSON-serializable `gathered_agents` shape `assemble_agent_window_rows`
        consumes, so a caller may either assemble it locally (the default,
        `agent_window_status_payloads`) or batch it into a jobd `tabber_activity_view` submission
        for sessions whose signature changed, deferring only the pure assembly/sort to the worker.
        """
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
        gathered_agents: list[dict[str, Any]] = []
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
            gathered_agents.append({
                "kind": kind,
                "state": state,
                "window": window,
                "window_index": window_index,
                "window_name": window_name,
                "window_label": window_label,
                "pane": pane,
                "pane_target": str(agent.pane_target or ""),
                "pid": pid,
                "window_is_current": window_is_current,
                "paths": paths,
                "path_entries": path_entries,
                "fallback_path": fallback_path,
                "git": copy.deepcopy(path_record.get("git")) if isinstance(path_record, dict) and isinstance(path_record.get("git"), dict) else None,
                "transcript": str(agent.transcript or ""),
                "transcript_id": self.agent_transcript_id(agent),
                "agent_session_id": str(agent.session_id or ""),
                "elapsed": elapsed,
                "last_active_ts": last_active_ts,
                "working_stopped_ts": working_stopped_ts,
                "observed_ts": observed_ts,
                "screen_text": str(screen.get("text") or ""),
                "status_tokens": screen.get("status_tokens") if isinstance(screen.get("status_tokens"), (int, float)) else None,
                "agent_index": agent_index,
                "attention_key": attention_key,
                "attention_acknowledged": self.attention_acknowledged(attention_key) if attention_key else None,
                "attention_acknowledged_at": self.attention_acknowledged_at(attention_key) if attention_key else None,
                "cooldown_attention_key": cooldown_attention_key,
                "cooldown_acknowledged": self.attention_acknowledged(cooldown_attention_key) if cooldown_attention_key else None,
                "cooldown_acknowledged_at": self.attention_acknowledged_at(cooldown_attention_key) if cooldown_attention_key else None,
                "owned": (owned_rows_by_target or {}).get((session, self.agent_window_index_key(window), str(agent.pane_target or ""), kind)),
            })
        return gathered_agents

    def auto_approve_session_status(
        self,
        session: str,
        discovered_sessions: dict[str, SessionInfo] | None = None,
        include_live_prompt: bool = True,
        capture_bare_session_when_roster: bool = False,
        activity_snapshot: dict[str, Any] | None = None,
        timings: dict[str, float] | None = None,
        preclassified_by_target: dict[str, dict[str, Any]] | None = None,
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
        classification = preclassified_by_target.get(capture_target) if preclassified_by_target else None
        if isinstance(classification, dict) and isinstance(classification.get("screen"), dict):
            prompt = normalized_prompt_state(classification.get("prompt"))
            screen = dict(classification["screen"])
        else:
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
            preclassified_by_target=preclassified_by_target or ({capture_target: screen} if capture_target else None),
            include_path_metadata=False,
        )
        add_phase_timing(timings, "agent_windows", agent_windows_started)
        return payload

    def status_roster_pane_classifications(
        self,
        discovered_sessions: dict[str, SessionInfo],
        rebuild_sessions: set[str],
        *,
        pane_source_signatures: dict[str, str] | None,
        capture_targets: set[str] | None,
    ) -> tuple[dict[str, dict[str, Any]], int]:
        if pane_source_signatures is not None:
            source_targets = set(pane_source_signatures)
            self.status_pane_classification_cache = {
                target: record
                for target, record in self.status_pane_classification_cache.items()
                if target in source_targets
            }
        else:
            self.status_pane_classification_cache.clear()

        targets: dict[str, str] = {}
        for session in self.sessions:
            if session not in rebuild_sessions:
                continue
            target = self.auto_approve_capture_target(session, discovered_sessions=discovered_sessions)
            if target:
                targets.setdefault(target, session)
            info = discovered_sessions.get(session)
            if info is None:
                continue
            for agent in info.agents:
                agent_target = str(agent.pane_target or "")
                if agent_target:
                    targets.setdefault(agent_target, session)

        classifications: dict[str, dict[str, Any]] = {}
        capture_count = 0
        for target, session in targets.items():
            source_signature = pane_source_signatures.get(target) if pane_source_signatures is not None else None
            cached = self.status_pane_classification_cache.get(target)
            cache_matches = (
                source_signature is not None
                and isinstance(cached, dict)
                and cached.get("source_signature") == source_signature
                and isinstance(cached.get("screen"), dict)
            )
            must_capture = (
                pane_source_signatures is None
                or source_signature is None
                or not cache_matches
                or capture_targets is None
                or target in capture_targets
            )
            if must_capture:
                classification = self.roster_pane_classification(
                    session,
                    target,
                    discovered_sessions=discovered_sessions,
                )
                capture_count += 1
                if source_signature is not None:
                    cached = {
                        "source_signature": source_signature,
                        "prompt": dict(classification["prompt"]),
                        "screen": dict(classification["screen"]),
                    }
                    self.status_pane_classification_cache[target] = cached
                else:
                    cached = classification
            classifications[target] = {
                "prompt": dict(cached.get("prompt") or normalized_prompt_state()),
                "screen": dict(cached["screen"]),
            }
        return classifications, capture_count

    def build_auto_approve_status(
        self,
        session: str | None = None,
        timings: dict[str, float] | None = None,
        *,
        sync_workers: bool = True,
        session_payload_cache: dict[str, Any] | None = None,
        capture_sessions: set[str] | None = None,
        pane_source_signatures: dict[str, str] | None = None,
        capture_targets: set[str] | None = None,
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
        self.prune_absent_agent_window_transition_state(discovered_sessions)
        add_phase_timing(timings, "discover_sessions", discover_started)
        sessions_started = time.perf_counter()
        cached = session_payload_cache or {}
        rebuild_sessions = {
            name
            for name in self.sessions
            if capture_sessions is None or name in capture_sessions or not isinstance(cached.get(name), dict)
        }
        preclassified_by_target, pane_capture_count = self.status_roster_pane_classifications(
            discovered_sessions,
            rebuild_sessions,
            pane_source_signatures=pane_source_signatures,
            capture_targets=capture_targets,
        )
        if timings is not None:
            timings["pane_capture_count"] = float(pane_capture_count)
        sessions_payload = {}
        for name in self.sessions:
            cached_payload = cached.get(name)
            if capture_sessions is not None and name not in capture_sessions and isinstance(cached_payload, dict):
                sessions_payload[name] = dict(cached_payload)
                continue
            sessions_payload[name] = self.auto_approve_session_status(
                name,
                discovered_sessions=discovered_sessions,
                include_live_prompt=False,
                capture_bare_session_when_roster=True,
                activity_snapshot=activity_snapshot,
                timings=timings,
                preclassified_by_target=preclassified_by_target,
            )
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

    def auto_approve_status_bytes(self, session: str | None = None) -> tuple[bytes, HTTPStatus]:
        """Return daemon-owned status bytes without web-side discovery or encoding."""

        # Attention-ack ownership stays in WEB, not statusd. Cross-process push and SSE polling
        # are latency optimizations, not correctness boundaries: a peer can miss both while
        # disconnected. Merge the shared acknowledgement revision on every explicit read and, when
        # it advances, invalidate statusd so the next snapshot rebuilds against the acked revision.
        if self.merge_shared_attention_acks():
            self.status_client.invalidate("auto_approve")
        response, body = self.status_client.snapshot(self.sessions, session=session, timeout=1.0)
        if response.get("ok") is not True or not body:
            if local_service_failure_is_transient(response):
                raw_status = int(response.get("status") or HTTPStatus.SERVICE_UNAVAILABLE)
                status = (
                    HTTPStatus(raw_status)
                    if HTTPStatus.BAD_REQUEST <= raw_status <= 599
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )
                return json.dumps(response, separators=(",", ":")).encode("utf-8"), status
            diagnostic = "status service unavailable"
            payload = {
                "status": "unavailable",
                **user_message_payload("common.requestFailed", diagnostic),
                "terminal": True,
            }
            raw_status = int(response.get("status") or HTTPStatus.SERVICE_UNAVAILABLE)
            status = HTTPStatus.FAILED_DEPENDENCY if raw_status >= 500 else HTTPStatus(raw_status)
            return json.dumps(payload, separators=(",", ":")).encode("utf-8"), status
        try:
            metadata = validate_status_snapshot(response, body)
        except StatusProtocolError:
            diagnostic = "status service upgrade required"
            payload = {
                "status": "upgrade_required",
                **user_message_payload("common.requestFailed", diagnostic),
            }
            return json.dumps(payload, separators=(",", ":")).encode("utf-8"), HTTPStatus.UPGRADE_REQUIRED
        return body, HTTPStatus(metadata.status)

    def stop_auto_approve_all(self) -> None:
        self.pricing_refresh_coordinator.stop_periodic()
        self.stats_current_runtime.stop()
        self.stop_jobd_operation_service()
        self.job_client.stop_for_scheduler()
        self.approval_client.request({"action": "shutdown"}, timeout=2.5)
        port = int(self.background_owner.port or 0)
        if port:
            local_services_registry.shutdown_owned_local_services(port, common.RUNTIME_DIR / "services")
        self.background_owner.stop()
        self.yoagent_controller.close_yoagent_codex_app_server()
        self.control_server.stop()

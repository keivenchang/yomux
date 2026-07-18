# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Interactive browser terminals for local tmux sessions.

This starts a local HTTP/WebSocket server and attaches one PTY-backed tmux
client per browser panel. The server is intentionally dependency-free on the
Python side so it can run from a normal host checkout.
"""

from __future__ import annotations

import collections
import logging
import math
import os
import re
import signal
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable
from typing import TypedDict
from zoneinfo import ZoneInfo

from . import auth as _auth
from .cache import MISS as cache_MISS
from .locales import user_message_payload
from .runtime_env import healed_runtime_path
from .tmux_utils import list_tmux_session_names
from .tmux_utils import run_cmd
from .tmux_utils import unique_session_names


DEFAULT_SESSIONS: tuple[str, ...] = ()
DEFAULT_COLS = 120
DEFAULT_ROWS = 36
MAX_TRANSCRIPT_TAIL_LINES = 5000
MAX_COMPACT_TRANSCRIPT_ITEMS = 200
MAX_YOLOMUX_SESSION_TABS = 99
ACTIVITY_MAX_HOURS = 24.0 * 365.0
YOLOMUX_VERSION = "0.6.3"
UPDATE_NOTIFY_LEVELS: tuple[str, ...] = ("major", "minor", "patch", "none")
SUMMARY_LOOKBACK_SECONDS = 3600
SUMMARY_MAX_PROMPT_CHARS = 100_000
SUMMARY_CODEX_TIMEOUT_SECONDS = 600
SUMMARY_CODEX_MODEL = os.environ.get("YOLOMUX_SUMMARY_MODEL", "gpt-5.5")
SUMMARY_CODEX_EFFORT = os.environ.get("YOLOMUX_SUMMARY_EFFORT", "low")
SUMMARY_CODEX_SERVICE_TIER = os.environ.get("YOLOMUX_SUMMARY_SERVICE_TIER", "fast")
YOAGENT_CLAUDE_SUMMARY_MODEL = os.environ.get("YOLOMUX_YOAGENT_CLAUDE_SUMMARY_MODEL", "claude-haiku-4-5")
CONFIG_DIR = Path(os.environ.get("YOLOMUX_CONFIG_DIR", str(Path.home() / ".config" / "yolomux")))
STATE_DIR = Path(os.environ.get("YOLOMUX_STATE_DIR", str(Path.home() / ".local" / "state" / "yolomux")))
# Reconstructible provider metadata belongs in the cache root, rather than in
# STATE_DIR alongside user activity/history.  Keep this as the one owner of
# the path so tests and future cache consumers do not grow ad-hoc ~/.cache
# literals.
_DEFAULT_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
YOLOMUX_CACHE_DIR = Path(os.environ.get("YOLOMUX_CACHE_DIR", str(_DEFAULT_XDG_CACHE_HOME / "yolomux"))).expanduser()
MODEL_PRICING_CACHE_DIR = YOLOMUX_CACHE_DIR / "model-pricing"
MODEL_PRICING_DATABASE_PATH = MODEL_PRICING_CACHE_DIR / "pricing.sqlite3"
YOAGENT_CODEX_HOME = Path(os.environ.get("YOLOMUX_CODEX_HOME") or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
STATE_PATH = CONFIG_DIR / "state.json"
EVENT_LOG_PATH = STATE_DIR / "events.jsonl"
RUN_HISTORY_PATH = STATE_DIR / "run-history.json"
ACTIVITY_PATH = STATE_DIR / "activity.json"
TMUX_AI_STATUS_PATH = STATE_DIR / "tmux-AI-status.json"
LEGACY_ATTENTION_ACKS_PATH = STATE_DIR / "attention-acks.json"
ACTIVITY_HEARTBEATS_PATH = STATE_DIR / "activity-heartbeats.jsonl"
WATCH_INDEX_PATH = STATE_DIR / "watch-index.json"
AUTO_APPROVE_LOCK_DIR = STATE_DIR / "locks"
CONTROL_SOCKET_DIR = STATE_DIR / "control"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AGENT_COMMANDS = {"claude", "codex", "term"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
TERMINAL_QUERY_RESPONSE_RE = re.compile(r"(?:\x1b\[[?>]?[0-9;]*c|\x1bP[>|!][^\x1b]*(?:\x1b\\|\x9c))")
LINEAR_ID_RE = re.compile(r"(?<![A-Za-z0-9])(?:DIS|DGH|DYN|OPS|INFRA)-\d{1,6}(?![A-Za-z0-9])")
YOLOMUX_VERSION_ASSIGNMENT_RE = re.compile(r"^\s*YOLOMUX_VERSION\s*=\s*['\"]([^'\"]+)['\"]\s*$", re.MULTILINE)
SEMVER_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:\D.*)?$")


def start_thread_with_rollback(worker: threading.Thread, rollback: Callable[[], None]) -> None:
    """Start an installed worker, restoring its owning record if Thread.start fails."""
    try:
        worker.start()
    except Exception:
        rollback()
        raise


def file_revision(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "0-0"
    return f"{stat.st_mtime_ns}-{stat.st_size}"


def yolomux_client_revision() -> str:
    return file_revision(STATIC_DIR / "yolomux.js")


def yolomux_dev_bundle_revision() -> str:
    """Identify both browser assets so a restarted dev server can refresh stale clients."""
    return ".".join(file_revision(STATIC_DIR / asset) for asset in ("yolomux.js", "yolomux.css"))


class ErrorPayload(TypedDict, total=False):
    error: str
    user_message: dict[str, Any]
    diagnostic: str
    path: str
    session: str
    status: int


def error_payload(
    error: object,
    *,
    message_key: str = "",
    message_params: dict[str, Any] | None = None,
    diagnostic: object = "",
    **fields: Any,
) -> ErrorPayload:
    """Return one structured user-message shape while preserving raw diagnostic context.

    Typed request/filesystem errors pass their known fields explicitly through their own ``payload()``
    methods. Plain-string callers retain their raw fallback until they are assigned a catalog key.
    """
    fallback = str(error)
    key = str(message_key or "")
    params = message_params or {}
    raw_diagnostic = diagnostic
    payload: ErrorPayload = user_message_payload(key, fallback, **dict(params or {}))
    if raw_diagnostic:
        payload["diagnostic"] = str(raw_diagnostic)
    for field_name, value in fields.items():
        if value is not None:
            payload[field_name] = int(value) if field_name == "status" else value
    return payload
MAIN_BRANCHES = {"main", "master"}
METADATA_CACHE_TTL_SECONDS = 300
HTTP_METADATA_TIMEOUT_SECONDS = 2.0
MAX_EVENT_TAIL_LINES = 500
GITHUB_API_ROOT = "https://api.github.com"
LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_LINEAR_ISSUE_BASE_URL = "https://linear.app/issue"
OTHER_BRANCH_LIMIT = 8
# the cache-miss sentinel is owned by cache.py (where the single TtlCache lives) and re-exported
# here for the modules that import it from common. Same object identity, so `is _CACHE_MISS` holds.
_CACHE_MISS = cache_MISS
SERVER_HOSTNAME = socket.gethostname()
SERVER_STARTED_AT = time.time()
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_YOLOMUX_COMMIT_TIME_PT: str | None = None
_YOLOMUX_COMMIT_SHA: str | None = None
_YOLOMUX_COMMIT_COUNT: int | None = None
_AGENT_PATH_WARNING_KEYS: set[str] = set()


def heal_server_path() -> str:
    """Make agent CLIs installed under ~/.local/bin visible under stripped service environments."""
    os.environ["PATH"] = healed_runtime_path(os.environ, home=Path.home())
    return os.environ["PATH"]


def codex_home_from_env(env: dict[str, str] | None = None) -> Path:
    values = env or os.environ
    configured = str(values.get("YOLOMUX_CODEX_HOME") or values.get("CODEX_HOME") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def codex_runtime_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build the Codex subprocess environment used by YO!agent."""
    env = dict(os.environ)
    if base_env is not None:
        env.update(base_env)
    codex_home = codex_home_from_env(env)
    codex_home.mkdir(parents=True, exist_ok=True)
    env["PATH"] = healed_runtime_path(env, home=Path.home())
    env["CODEX_HOME"] = str(codex_home)
    env["TERM"] = "xterm-256color"
    env["NO_COLOR"] = "1"
    return env


def warn_unavailable_agent_commands_once(agents: tuple[str, ...] = ("claude", "codex")) -> None:
    path = heal_server_path()
    logger = logging.getLogger(__name__)
    for agent in agents:
        if shutil.which(agent):
            continue
        if agent in _AGENT_PATH_WARNING_KEYS:
            continue
        _AGENT_PATH_WARNING_KEYS.add(agent)
        logger.warning("%s not found on server PATH=%s; agent will be greyed in the UI", agent, path)


heal_server_path()


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def positive_finite_number(value: Any) -> float:
    """Normalize counters and rates that cannot be negative, infinite, or NaN."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def path_mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def codex_exec_argv(
    *,
    resume_session_id: str | None = None,
    ephemeral: bool = False,
    model: str | None = None,
    effort: str | None = None,
    service_tier: str | None = None,
    search: bool = False,
) -> list[str]:
    selected_model = str(model or SUMMARY_CODEX_MODEL).strip() or SUMMARY_CODEX_MODEL
    selected_effort = str(effort or SUMMARY_CODEX_EFFORT).strip() or SUMMARY_CODEX_EFFORT
    selected_service_tier = str(service_tier or SUMMARY_CODEX_SERVICE_TIER).strip() or SUMMARY_CODEX_SERVICE_TIER
    common = [
        "--json",
        "-m",
        selected_model,
        "-c",
        f'model_reasoning_effort="{selected_effort}"',
        "-c",
        f'service_tier="{selected_service_tier}"',
        "--ignore-rules",
    ]
    if resume_session_id:
        # `codex exec resume` restores the original cwd/sandbox and rejects --sandbox/--cd.
        return ["codex", "exec", "resume", *common, resume_session_id, "-"]
    args = ["codex"]
    if search:
        # `--search` is a top-level Codex flag in 0.141.0; `codex exec --search` is rejected.
        args.append("--search")
    args.extend(["exec", *common, "--sandbox", "read-only"])
    if ephemeral:
        args.append("--ephemeral")
    return [*args, "--cd", str(PROJECT_ROOT), "-"]


def codex_event_kind(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "")
    if event_type in {"thread.started", "turn.started"}:
        return "log"
    if event_type == "turn.completed":
        return "completed"
    if event_type in {"error", "turn.failed"}:
        return "error"
    return "content"


AuthUser = _auth.AuthUser
AuthIdentity = _auth.AuthIdentity
AUTH_CONFIG_PATH = _auth.AUTH_CONFIG_PATH
AUTH_CONFIG_DISPLAY_PATH = _auth.AUTH_CONFIG_DISPLAY_PATH
PLACEHOLDER_AUTH_USERNAME = _auth.PLACEHOLDER_AUTH_USERNAME
PLACEHOLDER_AUTH_PASSWORD = _auth.PLACEHOLDER_AUTH_PASSWORD
GUEST_AUTH_USERNAME = _auth.GUEST_AUTH_USERNAME
GUEST_AUTH_PASSWORD = _auth.GUEST_AUTH_PASSWORD
TEST_AUTH_BYPASS_ENV = _auth.TEST_AUTH_BYPASS_ENV
AUTH_COOKIE_NAME = _auth.AUTH_COOKIE_NAME
AUTH_LOGOUT_COOKIE_NAME = _auth.AUTH_LOGOUT_COOKIE_NAME
AUTH_COOKIE_MAX_AGE_SECONDS = _auth.AUTH_COOKIE_MAX_AGE_SECONDS
AUTH_COOKIE_SECRET_PATH = _auth.AUTH_COOKIE_SECRET_PATH
AUTH_COOKIE_SECRET = _auth.AUTH_COOKIE_SECRET
AUTH_CONFIG = _auth.AUTH_CONFIG
yaml_quote = _auth.yaml_quote
yaml_scalar = _auth.yaml_scalar
strip_yaml_comment = _auth.strip_yaml_comment
parse_yaml_key_value = _auth.parse_yaml_key_value
normalize_auth_role = _auth.normalize_auth_role
auth_user_from_mapping = _auth.auth_user_from_mapping
parse_auth_yaml = _auth.parse_auth_yaml
auth_config_text = _auth.auth_config_text
auth_password_is_hash = _auth.auth_password_is_hash
auth_password_matches = _auth.auth_password_matches
read_auth_users = _auth.read_auth_users
login_username = _auth.login_username
random_auth_password = _auth.random_auth_password
commented_auth_config_text = _auth.commented_auth_config_text
legacy_placeholder_auth_active = _auth.legacy_placeholder_auth_active
write_auth_config = _auth.write_auth_config
secure_auth_config_permissions = _auth.secure_auth_config_permissions


def _sync_auth_overrides() -> None:
    _auth.AUTH_CONFIG_PATH = AUTH_CONFIG_PATH
    _auth.AUTH_COOKIE_SECRET = AUTH_COOKIE_SECRET
    _auth.login_username = login_username
    _auth.random_auth_password = random_auth_password


def starter_auth_users() -> tuple[AuthUser, ...]:
    _sync_auth_overrides()
    return _auth.starter_auth_users()


def initialize_auth_config(path: Path) -> tuple[AuthUser, ...]:
    _sync_auth_overrides()
    return _auth.initialize_auth_config(path)


def current_auth_users() -> tuple[AuthUser, ...]:
    _sync_auth_overrides()
    return _auth.current_auth_users()


def auth_setup_required() -> bool:
    _sync_auth_overrides()
    return _auth.auth_setup_required()


def test_auth_bypass_enabled() -> bool:
    return _auth.test_auth_bypass_enabled()


def load_auth_cookie_secret(path: Path | None = None) -> bytes:
    return _auth.load_auth_cookie_secret(AUTH_COOKIE_SECRET_PATH if path is None else path)


def auth_cookie_value(username: str, password: str) -> str:
    _sync_auth_overrides()
    return _auth.auth_cookie_value(username, password)


def auth_identity_for_credentials(username: str, password: str) -> AuthIdentity | None:
    _sync_auth_overrides()
    return _auth.auth_identity_for_credentials(username, password)


def yolomux_commit_time_pt() -> str:
    global _YOLOMUX_COMMIT_TIME_PT
    if _YOLOMUX_COMMIT_TIME_PT is not None:
        return _YOLOMUX_COMMIT_TIME_PT
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "show", "-s", "--format=%cI", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
        timestamp = result.stdout.strip()
        commit_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        _YOLOMUX_COMMIT_TIME_PT = commit_time.astimezone(PACIFIC_TIME).strftime("%Y-%m-%d %H:%M:%S PT")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        _YOLOMUX_COMMIT_TIME_PT = "commit time unavailable"
    return _YOLOMUX_COMMIT_TIME_PT


def yolomux_commit_sha() -> str:
    global _YOLOMUX_COMMIT_SHA
    if _YOLOMUX_COMMIT_SHA is not None:
        return _YOLOMUX_COMMIT_SHA
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
        _YOLOMUX_COMMIT_SHA = result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        _YOLOMUX_COMMIT_SHA = ""
    return _YOLOMUX_COMMIT_SHA


def yolomux_commit_count() -> int:
    global _YOLOMUX_COMMIT_COUNT
    if _YOLOMUX_COMMIT_COUNT is not None:
        return _YOLOMUX_COMMIT_COUNT
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-list", "--count", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
        _YOLOMUX_COMMIT_COUNT = max(0, int(result.stdout.strip()))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        _YOLOMUX_COMMIT_COUNT = 0
    return _YOLOMUX_COMMIT_COUNT


POPULAR_IDE_XTERM_APP_NAMES = [
    "".join(("Cur", "sor.app")),
    f"{' '.join(('Visual', 'Studio', 'Code'))}.app",
    f"{' '.join(('Visual', 'Studio', 'Code'))} - Insiders.app",
    "Windsurf.app",
]
POPULAR_IDE_SERVER_DIRS = (
    f".{''.join(('cur', 'sor'))}-server",
    f".{''.join(('vs', 'code'))}-server",
    f".{''.join(('vs', 'code'))}-server-insiders",
    ".windsurf-server",
)

XTERM_ASSET_ROOTS = [
    *(Path(item).expanduser() for item in os.environ.get("YOLOMUX_XTERM_ROOTS", "").split(os.pathsep) if item),
    STATIC_DIR / "xterm",
    Path.cwd() / "node_modules" / "@xterm" / "xterm",
    Path(__file__).resolve().parent / "node_modules" / "@xterm" / "xterm",
    *(Path("/Applications") / app_name / "Contents" / "Resources" / "app" / "node_modules" / "@xterm" / "xterm" for app_name in POPULAR_IDE_XTERM_APP_NAMES),
]


def positive_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


_FILE_TRANSFER_MAX_BYTES_DEFAULT = 300 * 1024 * 1024
if "YOLOMUX_FILE_TRANSFER_MAX_BYTES" in os.environ:
    FILE_TRANSFER_MAX_BYTES = positive_env_int("YOLOMUX_FILE_TRANSFER_MAX_BYTES", _FILE_TRANSFER_MAX_BYTES_DEFAULT)
else:
    FILE_TRANSFER_MAX_BYTES = positive_env_int("YOLOMUX_UPLOAD_MAX_BYTES", _FILE_TRANSFER_MAX_BYTES_DEFAULT)
UPLOAD_MAX_BYTES = FILE_TRANSFER_MAX_BYTES
UPLOAD_MAX_FILES = positive_env_int("YOLOMUX_UPLOAD_MAX_FILES", 16)
DEFAULT_UPLOAD_FILENAME_TEMPLATE = "{date:%Y%m%d}-{seq:03d}-{name}{ext}"
UPLOAD_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
PASTE_UPLOAD_NAME_RE = re.compile(r"^(?P<date>\d{8})-(?P<index>\d{3})(?P<suffix>\.[A-Za-z0-9]{1,8})$")
UPLOAD_GENERATED_NAME_RE = re.compile(r"^\d{8}-\d{3}(?:-[^/]+)?\.[A-Za-z0-9]{1,12}$")


def is_generated_upload_name(path: str | Path) -> bool:
    return bool(UPLOAD_GENERATED_NAME_RE.fullmatch(Path(path).name))


@dataclass(frozen=True)
class TmuxPaneInfo:
    session: str
    window: str
    pane: str
    pane_id: str
    target: str
    current_path: str
    command: str
    active: bool
    window_active: bool
    title: str
    pid: int
    process_label: str | None = None
    process_label_pid: int | None = None
    window_name: str = ""


# Compatibility import for third-party callers during the terminology migration. New backend code
# must use TmuxPaneInfo so it cannot be confused with a physical YOLOmux YOPane.
PaneInfo = TmuxPaneInfo


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    command: str


@dataclass(frozen=True)
class AgentInfo:
    session: str
    kind: str
    pid: int
    pane_target: str
    command: str
    cwd: str | None
    status: str | None
    session_id: str | None
    transcript: str | None
    error: str | None
    model: str | None = None


@dataclass(frozen=True)
class SessionInfo:
    session: str
    panes: list[TmuxPaneInfo]
    selected_pane: TmuxPaneInfo | None
    agents: list[AgentInfo]


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes


def default_session_names() -> list[str]:
    tmux_sessions, _ = list_tmux_session_names()
    return unique_session_names(tmux_sessions)


def next_numbered_session_name(existing_sessions: list[str]) -> str | None:
    if len(existing_sessions) >= MAX_YOLOMUX_SESSION_TABS:
        return None
    for index in range(1, MAX_YOLOMUX_SESSION_TABS + 1):
        session = str(index)
        if session not in existing_sessions:
            return session
    return None


# Cumulative per-verb git spawn counts (bounded by the small git verb set).
# Monotonic so readers can diff without a cross-thread reset race; sampled into
# session-files performance accounting (DOIT.optimize-backends).
GIT_COMMAND_COUNTS: dict[str, int] = {}


def git(args: list[str], cwd: str, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    verb = args[0] if args else ""
    GIT_COMMAND_COUNTS[verb] = GIT_COMMAND_COUNTS.get(verb, 0) + 1
    return run_cmd(["git", "-C", cwd, *args], timeout=timeout)


def git_ahead_behind_counts(cwd: str, left: str, right: str = "HEAD") -> tuple[int, int] | None:
    """(ahead, behind) of `right` relative to `left`, or None on git failure / unparseable output.

    ahead = commits in `right` not in `left`; behind = the reverse. Uses
    `git rev-list --left-right --count left...right`, where parts[0] is the left-only count (behind) and
    parts[1] the right-only count (ahead). metadata.py and session_files.py each parsed this
    with their own ref order + return shape; the left/right sign is the classic trap, so it lives once here.
    """
    result = git(["rev-list", "--left-right", "--count", f"{left}...{right}"], cwd)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]), int(parts[0])  # (ahead = right-only, behind = left-only)
    except ValueError:
        return None


def parse_yolomux_version_source(source: str) -> str | None:
    match = YOLOMUX_VERSION_ASSIGNMENT_RE.search(source)
    return match.group(1).strip() if match else None


def semver_parts(version: Any) -> tuple[int, int, int] | None:
    match = SEMVER_RE.match(str(version or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def version_change_level(current: Any, target: Any) -> str:
    current_parts = semver_parts(current)
    target_parts = semver_parts(target)
    if current_parts is None or target_parts is None or target_parts <= current_parts:
        return "none"
    if target_parts[0] != current_parts[0]:
        return "major"
    if target_parts[1] != current_parts[1]:
        return "minor"
    if target_parts[2] != current_parts[2]:
        return "patch"
    return "none"


def update_notify_level_allows(change_level: Any, notify_level: Any) -> bool:
    ranks = {"none": 0, "patch": 1, "minor": 2, "major": 3}
    threshold = str(notify_level or "patch")
    if threshold == "none":
        return False
    return ranks.get(str(change_level or "none"), 0) >= ranks.get(threshold, ranks["patch"])


def git_yolomux_version_at_ref(cwd: str, ref: str) -> tuple[str | None, str | None]:
    result = git(["show", f"{ref}:yolomux_lib/common.py"], cwd)
    if result.returncode != 0:
        return None, (result.stderr or f"git show {ref}:yolomux_lib/common.py failed").strip()[:300]
    version = parse_yolomux_version_source(result.stdout or "")
    if not version:
        return None, f"YOLOMUX_VERSION not found in {ref}:yolomux_lib/common.py"
    return version, None


def yolomux_version_parts(version: str) -> tuple[int, ...] | None:
    clean = version.strip()
    if not clean or not re.fullmatch(r"\d+(?:\.\d+)*", clean):
        return None
    return tuple(int(part) for part in clean.split("."))


def yolomux_version_is_newer(target: str, current: str) -> bool:
    target_parts = yolomux_version_parts(target)
    current_parts = yolomux_version_parts(current)
    if target_parts is None or current_parts is None:
        return target.strip() != current.strip()
    length = max(len(target_parts), len(current_parts))
    padded_target = target_parts + (0,) * (length - len(target_parts))
    padded_current = current_parts + (0,) * (length - len(current_parts))
    return padded_target > padded_current


def update_check_status(cwd: str, branch: str = "main", dryrun: bool = False, fetch: bool = True) -> dict[str, Any]:
    """Whether `origin/<branch>` has a newer YOLOMUX_VERSION than the running checkout.

    Reads `yolomux_lib/common.py` from the remote ref via git on the local checkout (reusing its
    existing credentials, so this works for private repos with no GitHub token). SHA and ahead/behind
    counts stay in the payload for diagnostics only; they do not decide whether to notify.
    """
    current_sha = yolomux_commit_sha()
    base = {"available": False, "ahead": 0, "behind": 0, "current": YOLOMUX_VERSION,
            "current_version": YOLOMUX_VERSION, "current_sha": current_sha, "target": None,
            "target_version": None, "target_sha": None, "branch": branch, "dryrun": dryrun, "error": None,
            "version_change_level": "none"}
    if dryrun:
        return {**base, "available": True, "behind": 1, "target": "dryrun",
                "target_version": "dryrun", "version_change_level": "patch"}
    if fetch:
        fetched = git(["fetch", "--quiet", "origin", branch], cwd)
        if fetched.returncode != 0:
            return {**base, "error": (fetched.stderr or "git fetch failed").strip()[:300]}
    counts = git_ahead_behind_counts(cwd, f"origin/{branch}")
    if counts is None:
        return {**base, "error": "git rev-list failed"}
    ahead, behind = counts
    target = git(["rev-parse", "--short=12", f"origin/{branch}"], cwd)
    target_sha = target.stdout.strip() if target.returncode == 0 else None
    target_version, version_error = git_yolomux_version_at_ref(cwd, f"origin/{branch}")
    if version_error:
        return {**base, "ahead": ahead, "behind": behind, "target_sha": target_sha, "error": version_error}
    change_level = version_change_level(YOLOMUX_VERSION, target_version)
    return {**base, "available": bool(target_version and yolomux_version_is_newer(target_version, YOLOMUX_VERSION)),
            "ahead": ahead, "behind": behind, "target": target_version, "target_version": target_version,
            "target_sha": target_sha, "version_change_level": change_level}


def git_bytes(args: list[str], cwd: str, timeout: float = 3.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=timeout, check=False)


def xterm_asset_path(asset: str) -> Path | None:
    # VDI boxes have no compatible Node/npm. boot.sh downloads these pinned UMD assets into
    # static/ there, so prefer that self-contained runtime package before node_modules/IDE roots.
    packaged_path = STATIC_DIR / asset
    if packaged_path.is_file():
        return packaged_path
    relpaths = {
        "xterm.js": Path("lib") / "xterm.js",
        "xterm.css": Path("css") / "xterm.css",
    }
    addon_relpaths = {
        "xterm-addon-unicode11.js": ("addon-unicode11", Path("lib") / "addon-unicode11.js"),
    }
    relpath = relpaths.get(asset)
    addon = addon_relpaths.get(asset)
    if relpath is None and addon is None:
        return None
    if relpath is not None:
        for root in XTERM_ASSET_ROOTS:
            path = root / relpath
            if path.exists():
                return path
    if addon is not None:
        package, addon_relpath = addon
        for root in XTERM_ASSET_ROOTS:
            for path in (
                root.parent / package / addon_relpath,
                root / package / addon_relpath,
                root / addon_relpath,
                root / addon_relpath.name,
            ):
                if path.exists():
                    return path
    for server_dir in POPULAR_IDE_SERVER_DIRS:
        if relpath is not None:
            for path in Path.home().glob(f"{server_dir}/bin/*/*/node_modules/@xterm/xterm/{relpath}"):
                if path.exists():
                    return path
            for path in Path.home().glob(f"{server_dir}/bin/*/node_modules/@xterm/xterm/{relpath}"):
                if path.exists():
                    return path
        if addon is not None:
            package, addon_relpath = addon
            for path in Path.home().glob(f"{server_dir}/bin/*/*/node_modules/@xterm/{package}/{addon_relpath}"):
                if path.exists():
                    return path
            for path in Path.home().glob(f"{server_dir}/bin/*/node_modules/@xterm/{package}/{addon_relpath}"):
                if path.exists():
                    return path
    return None


def split_csv(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                parts.append(item)
    return parts



def tail_file_lines(path: Path, lines: int) -> str:
    # read a bounded window backward from EOF instead of scanning the whole file front-to-
    # back. Transcripts are multi-hundred-MB JSONL and this is called on every metadata poll,
    # /api/context, /api/session-metadata, and the summary — a full re-scan each time was the hot path.
    want = min(max(1, lines), MAX_TRANSCRIPT_TAIL_LINES)
    chunk = 65536
    max_bytes = want * chunk  # generous per-line ceiling; never walk the entire huge file
    data = b""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        pos = handle.tell()
        while pos > 0 and data.count(b"\n") <= want and len(data) < max_bytes:
            step = min(chunk, pos)
            pos -= step
            handle.seek(pos)
            data = handle.read(step) + data
    text = data.decode("utf-8", errors="replace")
    return "".join(text.splitlines(keepends=True)[-want:])

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}

def terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2.0)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=2.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            return

def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"

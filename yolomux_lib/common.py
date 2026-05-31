# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Interactive browser terminals for local tmux sessions.

This starts a local HTTP/WebSocket server and attaches one PTY-backed tmux
client per browser panel. The server is intentionally dependency-free on the
Python side so it can run from a normal host checkout.
"""

from __future__ import annotations

import argparse
import base64
import collections
import fcntl
import html
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import termios
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from typing import Callable
from urllib.parse import parse_qs
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import auth as _auth


DEFAULT_SESSIONS: tuple[str, ...] = ()
DEFAULT_COLS = 120
DEFAULT_ROWS = 36
MAX_TRANSCRIPT_TAIL_LINES = 5000
MAX_COMPACT_TRANSCRIPT_ITEMS = 200
MAX_YOLOMUX_SESSION_TABS = 99
YOLOMUX_VERSION = "0.1.24"
SUMMARY_LOOKBACK_SECONDS = 3600
SUMMARY_MAX_PROMPT_CHARS = 100_000
SUMMARY_CODEX_TIMEOUT_SECONDS = 600
SUMMARY_CODEX_MODEL = os.environ.get("YOLOMUX_SUMMARY_MODEL", "gpt-5.5")
SUMMARY_CODEX_EFFORT = os.environ.get("YOLOMUX_SUMMARY_EFFORT", "low")
SUMMARY_CODEX_SERVICE_TIER = os.environ.get("YOLOMUX_SUMMARY_SERVICE_TIER", "fast")
CONFIG_DIR = Path(os.environ.get("YOLOMUX_CONFIG_DIR", str(Path.home() / ".config" / "yolomux")))
STATE_DIR = Path(os.environ.get("YOLOMUX_STATE_DIR", str(Path.home() / ".local" / "state" / "yolomux")))
STATE_PATH = CONFIG_DIR / "state.json"
EVENT_LOG_PATH = STATE_DIR / "events.jsonl"
AUTO_APPROVE_LOCK_DIR = STATE_DIR / "locks"
CONTROL_SOCKET_DIR = STATE_DIR / "control"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AGENT_COMMANDS = {"claude", "codex", "term"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
TERMINAL_QUERY_RESPONSE_RE = re.compile(r"(?:\x1b\[[?>]?[0-9;]*c|\x1bP[>|!][^\x1b]*(?:\x1b\\|\x9c))")
LINEAR_ID_RE = re.compile(r"(?<![A-Za-z0-9])(?:DIS|DGH|DYN|OPS|INFRA)-\d{1,6}(?![A-Za-z0-9])")
MAIN_BRANCHES = {"main", "master"}
METADATA_CACHE_TTL_SECONDS = 300
HTTP_METADATA_TIMEOUT_SECONDS = 2.0
MAX_EVENT_TAIL_LINES = 500
GITHUB_API_ROOT = "https://api.github.com"
LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_LINEAR_ISSUE_BASE_URL = "https://linear.app/nv/issue"
OTHER_BRANCH_LIMIT = 8
_CACHE_MISS = object()
SERVER_HOSTNAME = socket.gethostname()
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_YOLOMUX_COMMIT_TIME_PT: str | None = None


AuthUser = _auth.AuthUser
AuthIdentity = _auth.AuthIdentity
AUTH_CONFIG_PATH = _auth.AUTH_CONFIG_PATH
AUTH_CONFIG_DISPLAY_PATH = _auth.AUTH_CONFIG_DISPLAY_PATH
PLACEHOLDER_AUTH_USERNAME = _auth.PLACEHOLDER_AUTH_USERNAME
PLACEHOLDER_AUTH_PASSWORD = _auth.PLACEHOLDER_AUTH_PASSWORD
GUEST_AUTH_USERNAME = _auth.GUEST_AUTH_USERNAME
GUEST_AUTH_PASSWORD = _auth.GUEST_AUTH_PASSWORD
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


XTERM_ASSET_ROOTS = [
    *(Path(item).expanduser() for item in os.environ.get("YOLOMUX_XTERM_ROOTS", "").split(os.pathsep) if item),
    STATIC_DIR / "xterm",
    Path.cwd() / "node_modules" / "@xterm" / "xterm",
    Path(__file__).resolve().parent / "node_modules" / "@xterm" / "xterm",
    Path("/Applications") / "Cursor.app" / "Contents" / "Resources" / "app" / "node_modules" / "@xterm" / "xterm",
    Path("/Applications") / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "node_modules" / "@xterm" / "xterm",
    Path("/Applications") / "Visual Studio Code - Insiders.app" / "Contents" / "Resources" / "app" / "node_modules" / "@xterm" / "xterm",
    Path("/Applications") / "Windsurf.app" / "Contents" / "Resources" / "app" / "node_modules" / "@xterm" / "xterm",
]


def positive_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


UPLOAD_MAX_BYTES = positive_env_int("YOLOMUX_UPLOAD_MAX_BYTES", 100 * 1024 * 1024)
UPLOAD_MAX_FILES = positive_env_int("YOLOMUX_UPLOAD_MAX_FILES", 16)
UPLOAD_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
PASTE_UPLOAD_NAME_RE = re.compile(r"^(?P<date>\d{8})-(?P<index>\d{3})(?P<suffix>\.[A-Za-z0-9]{1,8})$")


@dataclass(frozen=True)
class PaneInfo:
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


@dataclass(frozen=True)
class SessionInfo:
    session: str
    panes: list[PaneInfo]
    selected_pane: PaneInfo | None
    agents: list[AgentInfo]


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes


def run_cmd(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")


def tmux(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return run_cmd(["tmux", *args], timeout=timeout)


def tmux_session_target(session: str) -> str:
    return f"{session}:"


def list_tmux_session_names() -> tuple[list[str], str | None]:
    result = tmux(["list-sessions", "-F", "#{session_name}"], timeout=3.0)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "tmux list-sessions failed").strip()
        return [], error
    sessions = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(set(sessions), key=session_sort_key), None


def tmux_has_exact_session(session: str) -> bool:
    sessions, error = list_tmux_session_names()
    return error is None and session in sessions


def session_sort_key(session: str) -> tuple[int, str, int]:
    match = re.fullmatch(r"yolomux(\d+)", session)
    if match:
        return 0, "yolomux", int(match.group(1))
    match = re.fullmatch(r"project(\d+)", session)
    if match:
        return 1, "project", int(match.group(1))
    return 2, session.lower(), 0


def default_session_names() -> list[str]:
    tmux_sessions, _ = list_tmux_session_names()
    return unique_session_names(tmux_sessions)


def unique_session_names(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        session = value.strip()
        if not session or session in seen:
            continue
        seen.add(session)
        result.append(session)
    return sorted(result, key=session_sort_key)


def next_numbered_session_name(existing_sessions: list[str]) -> str | None:
    if len(existing_sessions) >= MAX_YOLOMUX_SESSION_TABS:
        return None
    for index in range(1, MAX_YOLOMUX_SESSION_TABS + 1):
        session = str(index)
        if session not in existing_sessions:
            return session
    return None


def git(args: list[str], cwd: str, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    return run_cmd(["git", "-C", cwd, *args], timeout=timeout)


def xterm_asset_path(asset: str) -> Path | None:
    relpaths = {
        "xterm.js": Path("lib") / "xterm.js",
        "xterm.css": Path("css") / "xterm.css",
    }
    relpath = relpaths.get(asset)
    if relpath is None:
        return None
    for root in XTERM_ASSET_ROOTS:
        path = root / relpath
        if path.exists():
            return path
    for server_dir in (".cursor-server", ".vscode-server", ".vscode-server-insiders", ".windsurf-server"):
        for path in Path.home().glob(f"{server_dir}/bin/*/*/node_modules/@xterm/xterm/{relpath}"):
            if path.exists():
                return path
        for path in Path.home().glob(f"{server_dir}/bin/*/node_modules/@xterm/xterm/{relpath}"):
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
    keep = collections.deque(maxlen=max(1, lines))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            keep.append(line)
    return "".join(keep)

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

def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"

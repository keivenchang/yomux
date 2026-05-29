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
import getpass
import hashlib
import hmac
import html
import importlib.util
import json
import os
import pty
import re
import select
import shutil
import secrets
import signal
import socket
import struct
import subprocess
import sys
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


DEFAULT_SESSIONS: tuple[str, ...] = ()
DEFAULT_COLS = 120
DEFAULT_ROWS = 36
MAX_TRANSCRIPT_TAIL_LINES = 5000
MAX_COMPACT_TRANSCRIPT_ITEMS = 200
MAX_YOLOMUX_SESSION_TABS = 99
YOLOMUX_VERSION = "0.1.12"
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
AUTH_CONFIG_PATH = CONFIG_DIR / "auth.yaml"
AUTH_CONFIG_DISPLAY_PATH = "~/.config/yolomux/auth.yaml"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AGENT_COMMANDS = {"claude", "codex", "term"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
AUTO_APPROVE_SCRIPT = PROJECT_ROOT / "auto_approve_tmux.py"
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
PLACEHOLDER_AUTH_USERNAME = "user"
PLACEHOLDER_AUTH_PASSWORD = "password"
GUEST_AUTH_USERNAME = "guest"
GUEST_AUTH_PASSWORD = "guest"
SERVER_HOSTNAME = socket.gethostname()
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_YOLOMUX_COMMIT_TIME_PT: str | None = None


@dataclass(frozen=True)
class AuthUser:
    username: str
    password: str
    role: str


@dataclass(frozen=True)
class AuthIdentity:
    username: str
    password: str
    role: str


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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


def yaml_scalar(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def parse_yaml_key_value(line: str) -> tuple[str, str] | None:
    key, separator, value = line.partition(":")
    if not separator:
        return None
    key = key.strip()
    if not key:
        return None
    return key, yaml_scalar(value)


def normalize_auth_role(value: str) -> str:
    role = value.strip().lower()
    if role in {"admin", "readonly"}:
        return role
    if role in {"read-only", "read_only", "viewer", "view", "ro"}:
        return "readonly"
    return "readonly"


def auth_user_from_mapping(mapping: dict[str, str]) -> AuthUser | None:
    username = mapping.get("username", "").strip()
    password = mapping.get("password", "")
    role = normalize_auth_role(mapping.get("role", mapping.get("access", "readonly")))
    if not username or not password:
        return None
    return AuthUser(username=username, password=password, role=role)


def parse_auth_yaml(text: str) -> tuple[AuthUser, ...]:
    users: list[AuthUser] = []
    in_users = False
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = strip_yaml_comment(raw_line).rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "users:":
            in_users = True
            if current is not None:
                user = auth_user_from_mapping(current)
                if user:
                    users.append(user)
                current = None
            continue
        if not in_users:
            continue
        if stripped.startswith("- "):
            if current is not None:
                user = auth_user_from_mapping(current)
                if user:
                    users.append(user)
            current = {}
            remainder = stripped[2:].strip()
            if remainder:
                pair = parse_yaml_key_value(remainder)
                if pair:
                    current[pair[0]] = pair[1]
            continue
        if current is None and stripped.endswith(":"):
            current = {"username": yaml_scalar(stripped[:-1])}
            continue
        if current is None:
            continue
        pair = parse_yaml_key_value(stripped)
        if pair:
            current[pair[0]] = pair[1]
    if current is not None:
        user = auth_user_from_mapping(current)
        if user:
            users.append(user)
    return tuple(users)


def auth_config_text(users: tuple[AuthUser, ...]) -> str:
    lines = [
        "users:",
    ]
    for user in users:
        lines.extend(
            [
                f"  - username: {yaml_quote(user.username)}",
                f"    password: {yaml_quote(user.password)}",
                f"    role: {yaml_quote(user.role)}",
            ]
        )
    return "\n".join(lines) + "\n"


def read_auth_users(path: Path) -> tuple[AuthUser, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return parse_auth_yaml(text)


def login_username() -> str:
    username = getpass.getuser().strip()
    if username:
        return username
    return os.environ.get("USER", "").strip() or "admin"


def random_auth_password() -> str:
    return secrets.token_urlsafe(18)


def starter_auth_users() -> tuple[AuthUser, ...]:
    return (
        AuthUser(username=login_username(), password=random_auth_password(), role="admin"),
        AuthUser(username=GUEST_AUTH_USERNAME, password=GUEST_AUTH_PASSWORD, role="readonly"),
    )


def commented_auth_config_text(users: tuple[AuthUser, ...]) -> str:
    lines = [
        "YOLOmux authentication is disabled until one or more account entries are uncommented.",
        "Uncomment and edit the account entries below, save the file, then refresh the browser.",
        "Keep admin credentials private. The guest/guest account is readonly.",
        "",
    ]
    for line in auth_config_text(users).splitlines():
        if line == "users:":
            lines.append(line)
        else:
            lines.append(f"# {line}" if line else "#")
    return "\n".join((f"# {line}" if line else "#") if index < 4 else line for index, line in enumerate(lines)) + "\n"


def initialize_auth_config(path: Path) -> tuple[AuthUser, ...]:
    if path.exists():
        secure_auth_config_permissions(path)
        users = read_auth_users(path)
        if legacy_placeholder_auth_active(users):
            write_auth_config(path, commented_auth_config_text(starter_auth_users()))
            return ()
        return users
    write_auth_config(path, commented_auth_config_text(starter_auth_users()))
    return ()


def current_auth_users() -> tuple[AuthUser, ...]:
    return initialize_auth_config(AUTH_CONFIG_PATH)


def legacy_placeholder_auth_active(users: tuple[AuthUser, ...]) -> bool:
    return len(users) == 1 and users[0].username == PLACEHOLDER_AUTH_USERNAME and users[0].password == PLACEHOLDER_AUTH_PASSWORD


def auth_setup_required() -> bool:
    users = current_auth_users()
    return not users or legacy_placeholder_auth_active(users)


AUTH_COOKIE_NAME = "yolomux_auth"
AUTH_LOGOUT_COOKIE_NAME = "yolomux_logged_out"
AUTH_COOKIE_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
AUTH_COOKIE_SECRET_PATH = CONFIG_DIR / "auth-cookie-secret"


def load_auth_cookie_secret(path: Path = AUTH_COOKIE_SECRET_PATH) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)
        raw = path.read_text(encoding="utf-8").strip()
        try:
            secret = bytes.fromhex(raw)
        except ValueError:
            secret = b""
        if len(secret) == 32:
            return secret
    secret = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{secret.hex()}\n")
    path.chmod(0o600)
    return secret


AUTH_COOKIE_SECRET = load_auth_cookie_secret()


def write_auth_config(path: Path, text: str) -> None:
    secure_auth_config_permissions(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


def secure_auth_config_permissions(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)


def auth_cookie_value(username: str, password: str) -> str:
    return hmac.new(
        AUTH_COOKIE_SECRET,
        f"{username}:{password}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def auth_identity_for_credentials(username: str, password: str) -> AuthIdentity | None:
    for user in current_auth_users():
        username_matches = hmac.compare_digest(username, user.username)
        password_matches = hmac.compare_digest(password, user.password)
        if username_matches and password_matches:
            return AuthIdentity(username=user.username, password=user.password, role=user.role)
    return None


AUTH_CONFIG = initialize_auth_config(AUTH_CONFIG_PATH)
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


_AUTO_APPROVE_MODULE: Any | None = None


def auto_approve_module() -> Any:
    global _AUTO_APPROVE_MODULE
    if _AUTO_APPROVE_MODULE is not None:
        return _AUTO_APPROVE_MODULE
    spec = importlib.util.spec_from_file_location("yolomux_auto_approve_tmux", AUTO_APPROVE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {AUTO_APPROVE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _AUTO_APPROVE_MODULE = module
    return module


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

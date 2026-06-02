from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import PROJECT_ROOT


def resolved_upload_dir(path: Path, allow_home: bool = False) -> tuple[Path | None, bool]:
    try:
        resolved = path.expanduser().resolve()
        home = Path.home().resolve()
    except OSError:
        return None, False
    return resolved, resolved.is_dir() and (allow_home or resolved != home)

def session_workdir(session: str) -> Path:
    match = re.fullmatch(r"(?:yolomux|project)?(\d+)", session)
    session_index = match.group(1) if match else None
    if session_index == "6":
        dev_path = Path(os.environ.get("YOLOMUX_DEV_WORKDIR", str(PROJECT_ROOT)))
        if dev_path.is_dir():
            return dev_path
    repo_name = f"project{session_index}" if session_index else session
    workspace_base = Path(os.environ.get("YOLOMUX_WORKSPACE_BASE", str(Path.home() / "workspaces")))
    repo_path = workspace_base / repo_name
    return repo_path if repo_path.is_dir() else Path.home()

def numbered_session_workdir(session: str) -> Path | None:
    match = re.fullmatch(r"\d+", session)
    if not match:
        return None
    if session == "6":
        dev_path = Path(os.environ.get("YOLOMUX_DEV_WORKDIR", str(PROJECT_ROOT)))
        if dev_path.is_dir():
            return dev_path
    workspace_base = Path(os.environ.get("YOLOMUX_WORKSPACE_BASE", str(Path.home() / "workspaces")))
    repo_path = workspace_base / f"project{session}"
    return repo_path if repo_path.is_dir() else None

def agent_command(agent: str, dangerously_yolo: bool = False) -> str:
    if agent == "codex":
        return "codex --dangerously-bypass-approvals-and-sandbox" if dangerously_yolo else "codex"
    if agent == "term":
        return os.environ.get("SHELL") or "bash"
    return "claude --dangerously-skip-permissions" if dangerously_yolo else "claude"

def available_agent_commands() -> list[str]:
    agents = [agent for agent in ("claude", "codex") if shutil.which(agent)]
    return agents or ["term"]

# DOIT.6 #39: surface claude/codex login status. The CLIs have purpose-built non-interactive status
# commands; logout (or a missing binary) must NOT silently fall back to a deterministic agent without
# telling the user to log in. These are subprocess calls, so the result is cached with a short TTL —
# never probe per request.
AGENT_AUTH_PROBES = {
    "claude": ("claude", "auth", "status"),
    "codex": ("codex", "login", "status"),
}
AGENT_LOGIN_COMMANDS = {
    "claude": "claude auth login",
    "codex": "codex login",
}
# Output substrings that mean "installed but NOT logged in" even on a zero exit (best-effort fallback
# when there is no machine-readable flag to parse).
_AGENT_LOGGED_OUT_MARKERS = (
    "not logged in",
    "not authenticated",
    "logged out",
    "please log in",
    "please login",
    "no credentials",
    "no api key",
)
_AGENT_AUTH_PROBE_TIMEOUT = 4.0
_AGENT_AUTH_CACHE_TTL = 45.0
_agent_auth_cache: dict[str, tuple[float, dict[str, dict[str, bool]]]] = {}


def _logged_in_flag_from_json(stdout: str) -> bool | None:
    # `claude auth status` prints JSON with an authoritative loggedIn flag; trust it when present.
    text = stdout.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict) and "loggedIn" in data:
        return data["loggedIn"] is True
    return None


def _probe_agent_logged_in(agent: str) -> bool:
    probe = AGENT_AUTH_PROBES.get(agent)
    if not probe:
        return False
    try:
        result = subprocess.run(list(probe), capture_output=True, text=True, timeout=_AGENT_AUTH_PROBE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False
    parsed = _logged_in_flag_from_json(result.stdout or "")
    if parsed is not None:
        return parsed
    if result.returncode != 0:
        return False
    combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return not any(marker in combined for marker in _AGENT_LOGGED_OUT_MARKERS)


def agent_auth_status(force: bool = False) -> dict[str, dict[str, bool]]:
    now = time.monotonic()
    cached = _agent_auth_cache.get("status")
    if not force and cached is not None and now - cached[0] < _AGENT_AUTH_CACHE_TTL:
        return cached[1]
    agents = ("claude", "codex")
    installed = {agent: shutil.which(agent) is not None for agent in agents}
    to_probe = [agent for agent in agents if installed[agent]]
    logged_in = {agent: False for agent in agents}
    if to_probe:
        # Probe concurrently so a cold cache costs ~one probe, not the sum, on page load.
        with ThreadPoolExecutor(max_workers=len(to_probe)) as pool:
            for agent, result in zip(to_probe, pool.map(_probe_agent_logged_in, to_probe)):
                logged_in[agent] = result
    status = {agent: {"installed": installed[agent], "logged_in": logged_in[agent]} for agent in agents}
    _agent_auth_cache["status"] = (now, status)
    return status

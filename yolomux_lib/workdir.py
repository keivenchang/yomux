from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import PROJECT_ROOT
from .common import codex_runtime_env
from .common import heal_server_path


TERMINAL_COMMAND_CANDIDATES = ("bash", "zsh", "fish", "sh", "dash", "ksh", "csh", "tcsh", "nu", "pwsh", "tsh")
SYSTEM_SHELLS_PATH = Path("/etc/shells")


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
    dev_workdir = session_dev_workdir(session_index)
    if dev_workdir is not None:
        return dev_workdir
    repo_name = f"project{session_index}" if session_index else session
    workspace_base = Path(os.environ.get("YOLOMUX_WORKSPACE_BASE", str(Path.home() / "workspaces")))
    repo_path = workspace_base / repo_name
    return repo_path if repo_path.is_dir() else Path.home()

def numbered_session_workdir(session: str) -> Path | None:
    match = re.fullmatch(r"\d+", session)
    if not match:
        return None
    return session_dev_workdir(session) or numbered_project_workdir(session)

def session_dev_workdir(session_index: str | None) -> Path | None:
    if not session_index:
        return None
    if session_index == "6":
        dev_path = Path(os.environ.get("YOLOMUX_DEV_WORKDIR", str(PROJECT_ROOT)))
        if dev_path.is_dir():
            return dev_path
    dev_path = Path.home() / f"yolomux.dev{session_index}"
    return dev_path if dev_path.is_dir() else None

def numbered_project_workdir(session: str) -> Path | None:
    workspace_base = Path(os.environ.get("YOLOMUX_WORKSPACE_BASE", str(Path.home() / "workspaces")))
    repo_path = workspace_base / f"project{session}"
    return repo_path if repo_path.is_dir() else None

def terminal_command_paths() -> dict[str, str]:
    """Return the host-approved interactive commands by short name, never user input paths."""
    heal_server_path()
    paths: dict[str, str] = {}
    candidates = [Path(os.environ.get("SHELL", "")).name, *TERMINAL_COMMAND_CANDIDATES]
    try:
        candidates.extend(
            Path(line.strip()).name
            for line in SYSTEM_SHELLS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    except OSError:
        pass
    for name in candidates:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name) or name == "tmux":
            continue
        command_path = shutil.which(name)
        if command_path:
            paths.setdefault(name, command_path)
    return paths


def available_terminal_commands() -> list[str]:
    return sorted(terminal_command_paths(), key=str.casefold)


def terminal_command(name: str | None = None) -> str | None:
    if not name:
        return os.environ.get("SHELL") or "bash"
    return terminal_command_paths().get(name)


def agent_command(agent: str, dangerously_yolo: bool = False, terminal: str | None = None) -> str:
    if agent == "codex":
        return "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust" if dangerously_yolo else "codex"
    if agent == "term":
        return terminal_command(terminal) or (os.environ.get("SHELL") or "bash")
    # Deliberately NOT --bare: --bare makes Claude Code read auth strictly from
    # ANTHROPIC_API_KEY/apiKeyHelper and never from the OAuth credential file or
    # keychain, so a subscription/enterprise OAuth login shows "Not logged in".
    return "claude --dangerously-skip-permissions" if dangerously_yolo else "claude"

def available_agent_commands() -> list[str]:
    heal_server_path()
    agents = [agent for agent in ("claude", "codex") if shutil.which(agent)]
    # A plain terminal (a shell) is always launchable, so always offer Term — even when Claude/Codex
    # are installed (it used to be a no-agent fallback only, which left Term greyed "unavailable").
    return agents + ["term"]

# surface claude/codex login status. The CLIs have purpose-built non-interactive status
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
_AGENT_AUTH_PROBE_TIMEOUT = 8.0
_AGENT_AUTH_CACHE_TTL = 300.0
_AGENT_AUTH_REFRESH_MARGIN_SECONDS = 60.0
_AGENT_AUTH_UNKNOWN_CACHE_TTL = 15.0
_AGENT_AUTH_STALE_TTL = 3600.0
_agent_auth_lock = threading.RLock()
_agent_auth_status: dict[str, dict[str, object]] | None = None
_agent_auth_expires_at = 0.0
_agent_auth_stale_until = 0.0
_agent_auth_refreshing = False


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


def _probe_agent_logged_in(agent: str) -> bool | None:
    probe = AGENT_AUTH_PROBES.get(agent)
    if not probe:
        return False
    kwargs: dict[str, object] = {"capture_output": True, "text": True, "timeout": _AGENT_AUTH_PROBE_TIMEOUT}
    if agent == "codex":
        kwargs["env"] = codex_runtime_env()
    try:
        result = subprocess.run(list(probe), **kwargs)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    parsed = _logged_in_flag_from_json(result.stdout or "")
    if parsed is not None:
        return parsed
    combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if result.returncode != 0:
        return False if any(marker in combined for marker in _AGENT_LOGGED_OUT_MARKERS) else None
    return not any(marker in combined for marker in _AGENT_LOGGED_OUT_MARKERS)


def _default_agent_auth_status() -> dict[str, dict[str, object]]:
    agents = ("claude", "codex")
    installed = {agent: shutil.which(agent) is not None for agent in agents}
    status: dict[str, dict[str, object]] = {}
    for agent in agents:
        entry: dict[str, object] = {"installed": installed[agent], "logged_in": None if installed[agent] else False}
        if not installed[agent]:
            entry["unavailable_reason"] = "not-on-path"
        else:
            entry["unavailable_reason"] = "auth-unknown"
        status[agent] = entry
    return status


def _merge_auth_status_with_previous(
    status: dict[str, dict[str, object]],
    previous: dict[str, dict[str, object]] | None,
) -> dict[str, dict[str, object]]:
    if not previous:
        return status
    merged = deepcopy(status)
    for agent, entry in merged.items():
        prior = previous.get(agent)
        if not isinstance(prior, dict):
            continue
        if entry.get("installed") is True and entry.get("logged_in") is None and isinstance(prior.get("logged_in"), bool):
            entry["logged_in"] = prior["logged_in"]
            entry.pop("unavailable_reason", None)
    return merged


def _probe_agent_auth_status() -> dict[str, dict[str, object]]:
    heal_server_path()
    agents = ("claude", "codex")
    installed = {agent: shutil.which(agent) is not None for agent in agents}
    to_probe = [agent for agent in agents if installed[agent]]
    logged_in: dict[str, bool | None] = {agent: False for agent in agents}
    if to_probe:
        # Probe concurrently so a cold cache costs ~one probe, not the sum, on page load.
        with ThreadPoolExecutor(max_workers=len(to_probe)) as pool:
            for agent, result in zip(to_probe, pool.map(_probe_agent_logged_in, to_probe)):
                logged_in[agent] = result
    status = {}
    has_unknown = False
    for agent in agents:
        entry = {"installed": installed[agent], "logged_in": logged_in[agent]}
        if not installed[agent]:
            entry["unavailable_reason"] = "not-on-path"
        elif logged_in[agent] is None:
            has_unknown = True
            entry["unavailable_reason"] = "auth-unknown"
        status[agent] = entry
    return status


def _store_agent_auth_status(status: dict[str, dict[str, object]], *, preserve_previous_known: bool = False) -> dict[str, dict[str, object]]:
    global _agent_auth_status
    global _agent_auth_expires_at
    global _agent_auth_stale_until
    has_unknown = any(entry.get("installed") is True and entry.get("logged_in") is None for entry in status.values())
    now = time.monotonic()
    with _agent_auth_lock:
        stored = _merge_auth_status_with_previous(status, _agent_auth_status) if preserve_previous_known else status
        stored_has_unknown = any(entry.get("installed") is True and entry.get("logged_in") is None for entry in stored.values())
        ttl = _AGENT_AUTH_UNKNOWN_CACHE_TTL if stored_has_unknown or has_unknown else _AGENT_AUTH_CACHE_TTL
        _agent_auth_status = deepcopy(stored)
        _agent_auth_expires_at = now + ttl
        _agent_auth_stale_until = now + _AGENT_AUTH_STALE_TTL
        return deepcopy(stored)


def _refresh_agent_auth_status(*, preserve_previous_known: bool = False) -> dict[str, dict[str, object]]:
    return _store_agent_auth_status(_probe_agent_auth_status(), preserve_previous_known=preserve_previous_known)


def _cached_agent_auth_status(*, allow_stale: bool) -> tuple[dict[str, dict[str, object]] | None, bool, float]:
    now = time.monotonic()
    with _agent_auth_lock:
        if _agent_auth_status is None:
            return None, False, 0.0
        if _agent_auth_expires_at > now:
            return deepcopy(_agent_auth_status), True, _agent_auth_expires_at - now
        if allow_stale and _agent_auth_stale_until > now:
            return deepcopy(_agent_auth_status), False, 0.0
    return None, False, 0.0


def start_agent_auth_status_refresh(*, force: bool = False) -> bool:
    global _agent_auth_refreshing
    with _agent_auth_lock:
        if _agent_auth_refreshing:
            return False
        if not force and _agent_auth_status is not None and _agent_auth_expires_at - time.monotonic() > _AGENT_AUTH_REFRESH_MARGIN_SECONDS:
            return False
        _agent_auth_refreshing = True

    def refresh() -> None:
        global _agent_auth_refreshing
        try:
            _refresh_agent_auth_status(preserve_previous_known=True)
        finally:
            with _agent_auth_lock:
                _agent_auth_refreshing = False

    threading.Thread(target=refresh, name="agent-auth-status-refresh", daemon=True).start()
    return True


def agent_auth_status(
    force: bool = False,
    *,
    block: bool = True,
    allow_stale: bool = False,
    refresh: bool = False,
) -> dict[str, dict[str, object]]:
    heal_server_path()
    if force:
        return _refresh_agent_auth_status(preserve_previous_known=False)
    cached, fresh, refresh_in = _cached_agent_auth_status(allow_stale=allow_stale)
    if cached is not None:
        if refresh and (not fresh or refresh_in <= _AGENT_AUTH_REFRESH_MARGIN_SECONDS):
            start_agent_auth_status_refresh()
        return cached
    if not block:
        if refresh:
            start_agent_auth_status_refresh(force=True)
        return _default_agent_auth_status()
    return _refresh_agent_auth_status(preserve_previous_known=True)


def _clear_agent_auth_status_cache_for_tests() -> None:
    global _agent_auth_status
    global _agent_auth_expires_at
    global _agent_auth_stale_until
    global _agent_auth_refreshing
    with _agent_auth_lock:
        _agent_auth_status = None
        _agent_auth_expires_at = 0.0
        _agent_auth_stale_until = 0.0
        _agent_auth_refreshing = False

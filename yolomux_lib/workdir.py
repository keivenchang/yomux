from __future__ import annotations

import os
import re
import shutil
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

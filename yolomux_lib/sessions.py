from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .common import AGENT_COMMANDS
from .common import AgentInfo
from .common import PaneInfo
from .common import ProcessInfo
from .common import SessionInfo
from .cache import TtlCache
from .common import _CACHE_MISS
from .common import tail_file_lines
from .tmux_utils import cmd_error
from .tmux_utils import run_cmd
from .tmux_utils import tmux


TRANSCRIPT_LOOKUP_CACHE_TTL_SECONDS = 2.0
# Newest-by-name rollout files to consider per cwd lookup (bounds work on a large tree).
CODEX_TRANSCRIPT_SCAN_LIMIT = 80
# the shared TtlCache instead of a hand-rolled dict+lock+TTL. get_or_miss() preserves the
# _CACHE_MISS-vs-cached-None distinction the callers rely on.
_TRANSCRIPT_LOOKUP_CACHE = TtlCache(ttl_seconds=TRANSCRIPT_LOOKUP_CACHE_TTL_SECONDS)


def transcript_lookup_cache_key(kind: str, root: Path, needle: str) -> str:
    return "\x1f".join([kind, str(root.expanduser().resolve(strict=False)), needle])


def cached_transcript_lookup(kind: str, root: Path, needle: str) -> Path | None | object:
    return _TRANSCRIPT_LOOKUP_CACHE.get_or_miss(transcript_lookup_cache_key(kind, root, needle))


def set_cached_transcript_lookup(kind: str, root: Path, needle: str, path: Path | None) -> Path | None:
    _TRANSCRIPT_LOOKUP_CACHE.set(transcript_lookup_cache_key(kind, root, needle), path)
    return path


def list_tmux_panes() -> tuple[list[PaneInfo], str | None]:
    fmt = "\t".join(
        [
            "#{session_name}",
            "#{window_index}",
            "#{window_name}",
            "#{pane_index}",
            "#{pane_id}",
            "#{pane_current_path}",
            "#{pane_current_command}",
            "#{pane_active}",
            "#{window_active}",
            "#{pane_title}",
            "#{pane_pid}",
        ]
    )
    result = tmux(["list-panes", "-a", "-F", fmt])
    if result.returncode != 0:
        error = cmd_error(result, "tmux list-panes failed")
        return [], error

    panes: list[PaneInfo] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 11:
            continue
        session, window, window_name, pane, pane_id, path, command, active, window_active, title, pid_text = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        panes.append(
            PaneInfo(
                session=session,
                window=window,
                window_name=window_name,
                pane=pane,
                pane_id=pane_id,
                target=pane_id,
                current_path=path,
                command=command,
                active=active == "1",
                window_active=window_active == "1",
                title=title,
                pid=pid,
            )
        )
    return panes, None


def list_processes() -> tuple[dict[int, ProcessInfo], str | None]:
    result = run_cmd(["ps", "-eww", "-o", "pid=,ppid=,cmd="], timeout=8.0)
    if result.returncode != 0:
        error = cmd_error(result, "ps failed")
        return {}, error

    processes: dict[int, ProcessInfo] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        command = parts[2] if len(parts) == 3 else ""
        processes[pid] = ProcessInfo(pid=pid, ppid=ppid, command=command)
    return processes, None


def child_index(processes: dict[int, ProcessInfo]) -> dict[int, list[ProcessInfo]]:
    children: dict[int, list[ProcessInfo]] = {}
    for process in processes.values():
        children.setdefault(process.ppid, []).append(process)
    return children


def descendants(root_pid: int, children: dict[int, list[ProcessInfo]]) -> list[ProcessInfo]:
    found: list[ProcessInfo] = []
    stack = list(children.get(root_pid, []))
    while stack:
        process = stack.pop(0)
        found.append(process)
        stack.extend(children.get(process.pid, []))
    return found


def command_basename(command: str) -> str:
    if not command.strip():
        return ""
    first = command.strip().split(None, 1)[0]
    return Path(first).name.lower()


def command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def process_display_label(command: str) -> str:
    tokens = command_tokens(command)
    if not tokens:
        return ""
    base = Path(tokens[0]).name.lstrip("-")
    base_lower = base.lower()
    if base_lower.startswith("python") and len(tokens) > 1 and not tokens[1].startswith("-"):
        return Path(tokens[1]).name
    if base_lower == "node" and len(tokens) > 1 and not tokens[1].startswith("-"):
        return Path(tokens[1]).name
    return base


def command_option_value(command: str, long_name: str, short_name: str | None = None) -> str | None:
    tokens = command_tokens(command)
    for index, token in enumerate(tokens):
        if token == long_name and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(f"{long_name}="):
            return token.split("=", 1)[1]
        if short_name and token == short_name and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def agent_model_from_command(command: str) -> str | None:
    value = command_option_value(command, "--model", "-m")
    return value.strip("\"'") if isinstance(value, str) and value.strip("\"'") else None


def agent_model_from_metadata(metadata: dict[str, Any]) -> str | None:
    for key in ("model", "modelName", "model_name", "modelId", "model_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def pane_process_label(pane: PaneInfo, candidates: list[ProcessInfo]) -> tuple[str, int]:
    for process in candidates:
        label = process_display_label(process.command)
        if label in AGENT_COMMANDS or (label.startswith("mock_") and label.endswith(".py")):
            return label, process.pid
    for process in candidates:
        kind = classify_agent(process.command)
        if kind:
            return kind, process.pid
    for process in candidates:
        if process.pid == pane.pid:
            return process_display_label(process.command) or pane.command, process.pid
    return pane.command, pane.pid


def classify_agent(command: str) -> str | None:
    base = command_basename(command)
    if base in AGENT_COMMANDS:
        return base
    lowered = command.lower()
    if re.search(r"(^|\s)(claude|codex)(\s|$)", lowered):
        match = re.search(r"(^|\s)(claude|codex)(\s|$)", lowered)
        if match:
            return match.group(2)
    return None


def find_transcript_by_session_id(base_dir: Path, session_id: str) -> Path | None:
    if not base_dir.exists():
        return None
    cached = cached_transcript_lookup("claude-session-id", base_dir, session_id)
    if cached is not _CACHE_MISS:
        return cached
    for path in base_dir.glob(f"**/{session_id}.jsonl"):
        return set_cached_transcript_lookup("claude-session-id", base_dir, session_id, path)
    return set_cached_transcript_lookup("claude-session-id", base_dir, session_id, None)


def read_claude_agent(session: str, pane: PaneInfo, process: ProcessInfo) -> AgentInfo:
    meta_path = Path.home() / ".claude" / "sessions" / f"{process.pid}.json"
    if not meta_path.exists():
        return AgentInfo(
            session=session,
            kind="claude",
            pid=process.pid,
            pane_target=pane.target,
            command=process.command,
            cwd=None,
            status=None,
            session_id=None,
            transcript=None,
            error=f"missing {meta_path}",
            model=agent_model_from_command(process.command),
        )

    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return agent_error(session, "claude", pane, process, str(exc))
    except json.JSONDecodeError as exc:
        return agent_error(session, "claude", pane, process, f"invalid session metadata: {exc}")

    session_id = metadata.get("sessionId")
    transcript_path = None
    if isinstance(session_id, str) and session_id:
        transcript_path = find_transcript_by_session_id(Path.home() / ".claude" / "projects", session_id)

    return AgentInfo(
        session=session,
        kind="claude",
        pid=process.pid,
        pane_target=pane.target,
        command=process.command,
        cwd=metadata.get("cwd") if isinstance(metadata.get("cwd"), str) else None,
        status=metadata.get("status") if isinstance(metadata.get("status"), str) else None,
        session_id=session_id if isinstance(session_id, str) else None,
        transcript=str(transcript_path) if transcript_path else None,
        error=None if transcript_path else "claude transcript not found",
        model=agent_model_from_metadata(metadata) or agent_model_from_command(process.command),
    )


def agent_error(session: str, kind: str, pane: PaneInfo, process: ProcessInfo, error: str) -> AgentInfo:
    return AgentInfo(
        session=session,
        kind=kind,
        pid=process.pid,
        pane_target=pane.target,
        command=process.command,
        cwd=None,
        status=None,
        session_id=None,
        transcript=None,
        error=error,
        model=agent_model_from_command(process.command),
    )


def codex_transcript_header_cwd(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            line = handle.readline()
    except OSError:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    candidates = [
        record.get("cwd"),
        payload.get("cwd") if isinstance(payload, dict) else None,
    ]
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    return None


def find_recent_codex_transcript(cwd: str | None, root: Path | None = None) -> Path | None:
    root = root or Path.home() / ".codex" / "sessions"
    if not root.exists():
        return None
    if not cwd:
        return None
    cached = cached_transcript_lookup("codex-cwd", root, cwd)
    if cached is not _CACHE_MISS:
        return cached
    all_files = list(root.glob("**/rollout-*.jsonl"))
    files = sorted(all_files, key=lambda path: path.name, reverse=True)[:CODEX_TRANSCRIPT_SCAN_LIMIT]
    found = find_codex_transcript_in_candidates(files, cwd)
    if found is not None:
        return set_cached_transcript_lookup("codex-cwd", root, cwd, found)
    # Resumed Codex sessions keep the original rollout filename, so filename order can miss an old-name
    # transcript that was written seconds ago. Only pay the stat cost after the cheap filename pass misses.
    files_by_mtime = sorted(all_files, key=path_mtime, reverse=True)[:CODEX_TRANSCRIPT_SCAN_LIMIT]
    found = find_codex_transcript_in_candidates(files_by_mtime, cwd)
    if found is not None:
        return set_cached_transcript_lookup("codex-cwd", root, cwd, found)
    return set_cached_transcript_lookup("codex-cwd", root, cwd, None)


def recent_codex_transcript_candidates(root: Path | None = None, limit: int = CODEX_TRANSCRIPT_SCAN_LIMIT) -> list[Path]:
    root = root or Path.home() / ".codex" / "sessions"
    if not root.exists():
        return []
    all_files = list(root.glob("**/rollout-*.jsonl"))
    # Filename order is cheap and catches ordinary new sessions. Mtime order catches resumed sessions whose
    # old rollout filename is still being appended to today.
    candidates = [
        *sorted(all_files, key=lambda path: path.name, reverse=True)[:limit],
        *sorted(all_files, key=path_mtime, reverse=True)[:limit],
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_codex_transcript_in_candidates(files: list[Path], cwd: str) -> Path | None:
    for path in files:
        if codex_transcript_header_cwd(path) == cwd:
            return path
    for path in files:
        try:
            tail = tail_file_lines(path, 300)
        except OSError:
            continue
        if codex_transcript_tail_matches_cwd(tail, cwd):
            return path
    return None


def codex_transcript_tail_matches_cwd(tail: str, cwd: str) -> bool:
    for line in tail.splitlines():
        if codex_transcript_record_matches_cwd(line, cwd):
            return True
    return False


def codex_transcript_record_matches_cwd(line: str, cwd: str) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(record, dict):
        return False
    payload = record.get("payload")
    if record.get("cwd") == cwd:
        return True
    if isinstance(payload, dict) and payload.get("cwd") == cwd:
        return True
    arguments = payload.get("arguments") if isinstance(payload, dict) else None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = None
    if isinstance(arguments, dict):
        return arguments.get("cwd") == cwd or arguments.get("workdir") == cwd
    return False


def codex_transcript_session_id(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= 20:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if record.get("type") == "session_meta" and isinstance(payload, dict):
                    value = payload.get("id")
                    if isinstance(value, str) and value:
                        return value
                for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id", "conversationId"):
                    value = record.get(key)
                    if isinstance(value, str) and value:
                        return value
    except OSError:
        return None
    return None


def path_is_under(path: Path, root: Path) -> bool:
    resolved_path = path.expanduser().resolve(strict=False)
    resolved_root = root.expanduser().resolve(strict=False)
    return resolved_path == resolved_root or resolved_path.is_relative_to(resolved_root)


def codex_transcript_from_process_fd(pid: int, root: Path | None = None, fd_dir: Path | None = None) -> Path | None:
    root = root or Path.home() / ".codex" / "sessions"
    fd_dir = fd_dir or Path(f"/proc/{pid}/fd")
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return None
    candidates: list[Path] = []
    for entry in entries:
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.endswith(" (deleted)"):
            target = target[: -len(" (deleted)")]
        path = Path(target).expanduser()
        if not path.is_absolute():
            continue
        if path.name.startswith("rollout-") and path.suffix == ".jsonl" and path_is_under(path, root):
            candidates.append(path)
    candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
    return candidates[0] if candidates else None


def read_codex_agent(session: str, pane: PaneInfo, process: ProcessInfo) -> AgentInfo:
    proc_cwd = process_cwd(process.pid) or pane.current_path
    transcript_path = codex_transcript_from_process_fd(process.pid) or find_recent_codex_transcript(proc_cwd)
    session_id = codex_transcript_session_id(transcript_path)
    return AgentInfo(
        session=session,
        kind="codex",
        pid=process.pid,
        pane_target=pane.target,
        command=process.command,
        cwd=proc_cwd,
        status=None,
        session_id=session_id,
        transcript=str(transcript_path) if transcript_path else None,
        error=None if transcript_path else "codex transcript not found by process fd or cwd",
        model=agent_model_from_command(process.command),
    )


def process_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def pane_sort_key(pane: PaneInfo) -> tuple[str, int, int]:
    return (pane.session, int(pane.window), int(pane.pane))


def active_window_for_panes(panes: list[PaneInfo] | list[dict[str, Any]]) -> str | None:
    for pane in panes:
        if isinstance(pane, PaneInfo):
            if pane.window_active and pane.window not in (None, ""):
                return str(pane.window)
        elif isinstance(pane, dict) and pane.get("window_active") and pane.get("window") not in (None, ""):
            return str(pane.get("window"))
    return None


def preferred_pane(panes: list[PaneInfo], agents: list[AgentInfo]) -> PaneInfo | None:
    if not panes:
        return None
    agent_targets = {agent.pane_target for agent in agents}
    for pane in sorted(panes, key=pane_sort_key):
        if pane.window_active and pane.target in agent_targets:
            return pane
    for pane in sorted(panes, key=pane_sort_key):
        if pane.target in agent_targets:
            return pane
    for pane in sorted(panes, key=pane_sort_key):
        if pane.window_active and pane.command in AGENT_COMMANDS:
            return pane
    for pane in sorted(panes, key=pane_sort_key):
        if pane.command in AGENT_COMMANDS:
            return pane
    for pane in sorted(panes, key=pane_sort_key):
        if pane.window_active and pane.active:
            return pane
    for pane in sorted(panes, key=pane_sort_key):
        if pane.active:
            return pane
    return sorted(panes, key=pane_sort_key)[0]


def discover_sessions(sessions: list[str]) -> tuple[dict[str, SessionInfo], list[str]]:
    errors: list[str] = []
    panes, tmux_error = list_tmux_panes()
    if tmux_error:
        errors.append(tmux_error)
    processes, ps_error = list_processes()
    if ps_error:
        errors.append(ps_error)
    children = child_index(processes)

    by_session: dict[str, list[PaneInfo]] = {session: [] for session in sessions}
    for pane in panes:
        if pane.session in by_session:
            by_session[pane.session].append(pane)

    result: dict[str, SessionInfo] = {}
    for session in sessions:
        raw_session_panes = sorted(by_session.get(session, []), key=pane_sort_key)
        session_panes: list[PaneInfo] = []
        agents: list[AgentInfo] = []
        seen_pids: set[int] = set()
        for raw_pane in raw_session_panes:
            candidates = []
            root_process = processes.get(raw_pane.pid)
            if root_process:
                candidates.append(root_process)
            candidates.extend(descendants(raw_pane.pid, children))
            process_label, process_label_pid = pane_process_label(raw_pane, candidates)
            pane = replace(raw_pane, process_label=process_label, process_label_pid=process_label_pid)
            session_panes.append(pane)
            for process in candidates:
                kind = classify_agent(process.command)
                if not kind or process.pid in seen_pids:
                    continue
                seen_pids.add(process.pid)
                if kind == "claude":
                    agents.append(read_claude_agent(session, pane, process))
                elif kind == "codex":
                    agents.append(read_codex_agent(session, pane, process))
        result[session] = SessionInfo(
            session=session,
            panes=session_panes,
            selected_pane=preferred_pane(session_panes, agents),
            agents=agents,
        )
    return result, errors

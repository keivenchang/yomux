from __future__ import annotations

import ctypes
import json
import os
import platform
import shlex
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .common import AGENT_COMMANDS
from .common import AgentInfo
from .common import TmuxPaneInfo
from .common import ProcessInfo
from .common import SessionInfo
from .cache import TtlCache
from .common import _CACHE_MISS
from .common import tail_file_lines
from .common import path_mtime_or_zero
from .tmux_utils import cmd_error
from .tmux_utils import run_cmd
from .tmux_utils import tmux


TRANSCRIPT_LOOKUP_CACHE_TTL_SECONDS = 2.0
# Newest-by-name rollout files to consider per cwd lookup (bounds work on a large tree).
CODEX_TRANSCRIPT_SCAN_LIMIT = 80
CODEX_LSOF_TIMEOUT_SECONDS = 1.0
CODEX_LSOF_CACHE_SECONDS = 15.0
CODEX_LSOF_TRANSCRIPT_DESCRIPTOR_FILTER = "0-999"
CLAUDE_SUBAGENTS_DIRNAME = "subagents"
DARWIN_PROC_PIDVNODEPATHINFO = 9
DARWIN_MAXPATHLEN = 1024
# the shared TtlCache instead of a hand-rolled dict+lock+TTL. get_or_miss() preserves the
# _CACHE_MISS-vs-cached-None distinction the callers rely on.
_TRANSCRIPT_LOOKUP_CACHE = TtlCache(ttl_seconds=TRANSCRIPT_LOOKUP_CACHE_TTL_SECONDS)


class DarwinVinfoStat(ctypes.Structure):
    _fields_ = [
        ("vst_dev", ctypes.c_uint32),
        ("vst_mode", ctypes.c_uint16),
        ("vst_nlink", ctypes.c_uint16),
        ("vst_ino", ctypes.c_uint64),
        ("vst_uid", ctypes.c_uint32),
        ("vst_gid", ctypes.c_uint32),
        ("vst_atime", ctypes.c_int64),
        ("vst_atimensec", ctypes.c_int64),
        ("vst_mtime", ctypes.c_int64),
        ("vst_mtimensec", ctypes.c_int64),
        ("vst_ctime", ctypes.c_int64),
        ("vst_ctimensec", ctypes.c_int64),
        ("vst_birthtime", ctypes.c_int64),
        ("vst_birthtimensec", ctypes.c_int64),
        ("vst_size", ctypes.c_int64),
        ("vst_blocks", ctypes.c_int64),
        ("vst_blksize", ctypes.c_int32),
        ("vst_flags", ctypes.c_uint32),
        ("vst_gen", ctypes.c_uint32),
        ("vst_rdev", ctypes.c_uint32),
        ("vst_qspare", ctypes.c_int64 * 2),
    ]


class DarwinFsid(ctypes.Structure):
    _fields_ = [("val", ctypes.c_int32 * 2)]


class DarwinVnodeInfo(ctypes.Structure):
    _fields_ = [
        ("vi_stat", DarwinVinfoStat),
        ("vi_type", ctypes.c_int),
        ("vi_pad", ctypes.c_int),
        ("vi_fsid", DarwinFsid),
    ]


class DarwinVnodeInfoPath(ctypes.Structure):
    _fields_ = [
        ("vip_vi", DarwinVnodeInfo),
        ("vip_path", ctypes.c_char * DARWIN_MAXPATHLEN),
    ]


class DarwinProcVnodePathInfo(ctypes.Structure):
    _fields_ = [
        ("pvi_cdir", DarwinVnodeInfoPath),
        ("pvi_rdir", DarwinVnodeInfoPath),
    ]


def transcript_lookup_cache_key(kind: str, root: Path, needle: str) -> str:
    return "\x1f".join([kind, str(root.expanduser().resolve(strict=False)), needle])


def cached_transcript_lookup(kind: str, root: Path, needle: str) -> Path | None | object:
    return _TRANSCRIPT_LOOKUP_CACHE.get_or_miss(transcript_lookup_cache_key(kind, root, needle))


def set_cached_transcript_lookup(
    kind: str,
    root: Path,
    needle: str,
    path: Path | None,
    ttl: float | None = None,
) -> Path | None:
    _TRANSCRIPT_LOOKUP_CACHE.set(transcript_lookup_cache_key(kind, root, needle), path, ttl=ttl)
    return path


def list_tmux_panes() -> tuple[list[TmuxPaneInfo], str | None]:
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

    panes: list[TmuxPaneInfo] = []
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
            TmuxPaneInfo(
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
    result = run_cmd(["ps", "-eww", "-o", "pid=,ppid=,command="], timeout=8.0)
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


def pane_process_label(pane: TmuxPaneInfo, candidates: list[ProcessInfo]) -> tuple[str, int]:
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
    label = process_display_label(command).lower()
    if label in AGENT_COMMANDS:
        return label
    # Pane descendants include every search, test, and shell command launched by the agent. Only
    # classify an actual mock entry point here; an argument or commit message that merely mentions
    # "claude" or "codex" must not become a second agent for the same pane.
    if "--mock" in command_tokens(command):
        for kind in ("claude", "codex"):
            if label == f"{kind}.py":
                return kind
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


def claude_transcript_family_paths(path: Path) -> list[Path]:
    paths = [path]
    subagents_root = path.with_suffix("") / CLAUDE_SUBAGENTS_DIRNAME
    if subagents_root.is_dir():
        paths.extend(sorted(subagents_root.rglob("*.jsonl")))
    return paths


def codex_transcript_meta(path: Path) -> tuple[str, str]:
    """Return a rollout's thread id and immediate spawning parent, if transcript metadata has them."""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= 20:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
                    continue
                payload = record["payload"]
                thread_id = str(payload.get("id") or "").strip()
                source = payload.get("source")
                subagent = source.get("subagent") if isinstance(source, dict) else None
                thread_spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                parent_thread_id = str(thread_spawn.get("parent_thread_id") or "").strip() if isinstance(thread_spawn, dict) else ""
                return thread_id, parent_thread_id
    except OSError:
        return "", ""
    return "", ""


def codex_transcript_family_paths(path: Path, candidates: list[Path] | None = None) -> list[Path]:
    """Return one interactive rollout plus currently discoverable spawned descendants.

    Codex records a subagent in a separate rollout rather than below the parent's file. The
    parent thread id in each child ``session_meta`` is the durable linkage. We deliberately
    inspect the bounded recent-candidate window: active children move to the mtime front, while
    scanning every historical rollout on each ten-second stats pass would be an avoidable cost.
    """

    root = path.expanduser().resolve(strict=False)
    root_id, _parent_thread_id = codex_transcript_meta(root)
    if candidates is None and not root_id:
        return [root]
    sessions_root = next((ancestor for ancestor in root.parents if ancestor.name == "sessions"), None)
    window = list(candidates) if candidates is not None else recent_codex_transcript_candidates(root=sessions_root)
    paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in [root, *window]:
        resolved = candidate.expanduser().resolve(strict=False)
        if resolved in seen or not resolved.name.startswith("rollout-") or resolved.suffix != ".jsonl":
            continue
        seen.add(resolved)
        paths.append(resolved)
    metadata = {candidate: codex_transcript_meta(candidate) for candidate in paths}
    included_ids = {root_id}
    family = [root]
    # Iteration, rather than a one-level filter, includes grandchildren when all active rollout
    # files are in the candidate window.
    while True:
        additions = [
            candidate
            for candidate in paths
            if candidate not in family and metadata[candidate][1] in included_ids
        ]
        if not additions:
            break
        family.extend(additions)
        included_ids.update(metadata[candidate][0] for candidate in additions if metadata[candidate][0])
    return family


def read_claude_agent(
    session: str,
    pane: TmuxPaneInfo,
    process: ProcessInfo,
    *,
    sessions_root: Path | None = None,
    projects_root: Path | None = None,
) -> AgentInfo:
    sessions_root = sessions_root or Path.home() / ".claude" / "sessions"
    projects_root = projects_root or Path.home() / ".claude" / "projects"
    meta_path = sessions_root / f"{process.pid}.json"
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
        transcript_path = find_transcript_by_session_id(projects_root, session_id)

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


def select_claude_agent(
    session: str,
    pane: TmuxPaneInfo,
    processes: list[ProcessInfo],
    *,
    sessions_root: Path | None = None,
    projects_root: Path | None = None,
) -> AgentInfo | None:
    sessions_root = sessions_root or Path.home() / ".claude" / "sessions"
    candidates: list[AgentInfo] = []
    for process in processes:
        if classify_agent(process.command) != "claude" and not (sessions_root / f"{process.pid}.json").is_file():
            continue
        candidates.append(
            read_claude_agent(
                session,
                pane,
                process,
                sessions_root=sessions_root,
                projects_root=projects_root,
            )
        )
    if not candidates:
        return None
    with_transcript = [agent for agent in candidates if agent.transcript]
    if not with_transcript:
        return candidates[0]

    def active_session_rank(agent: AgentInfo) -> tuple[float, bool, int]:
        explicit_session_id = command_option_value(agent.command, "--session-id")
        explicit_session_match = bool(explicit_session_id and explicit_session_id == agent.session_id)
        transcript_activity = max(
            (path_mtime(path) for path in claude_transcript_family_paths(Path(str(agent.transcript)))),
            default=0.0,
        )
        # Claude's daemon-backed UI keeps the original launcher alive while a descendant owns the
        # active --session-id. Transcript-family activity owns selection; the explicit owner breaks ties.
        return transcript_activity, explicit_session_match, int(agent.pid or 0)

    return max(with_transcript, key=active_session_rank)


def agent_error(session: str, kind: str, pane: TmuxPaneInfo, process: ProcessInfo, error: str) -> AgentInfo:
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


path_mtime = path_mtime_or_zero


def newest_codex_transcript(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    return max(paths, key=path_mtime)


def find_codex_transcript_in_candidates(files: list[Path], cwd: str) -> Path | None:
    header_matches: list[Path] = []
    for path in files:
        if codex_transcript_header_cwd(path) == cwd:
            header_matches.append(path)
    if header_matches:
        return newest_codex_transcript(header_matches)
    tail_matches: list[Path] = []
    for path in files:
        try:
            tail = tail_file_lines(path, 300)
        except OSError:
            continue
        if codex_transcript_tail_matches_cwd(tail, cwd):
            tail_matches.append(path)
    return newest_codex_transcript(tail_matches)


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


def codex_rollout_paths(path_texts: list[str], root: Path) -> list[Path]:
    candidates: list[Path] = []
    for target in path_texts:
        if target.endswith(" (deleted)"):
            target = target[: -len(" (deleted)")]
        path = Path(target).expanduser()
        if not path.is_absolute():
            continue
        if path.name.startswith("rollout-") and path.suffix == ".jsonl" and path_is_under(path, root):
            candidates.append(path)
    return candidates


def lsof_paths_for_process(pid: int, descriptor: str | None = None, runner: Any = None) -> list[str]:
    args = ["lsof", "-p", str(pid)]
    if descriptor:
        args.extend(["-a", "-d", descriptor])
    args.append("-Fn")
    try:
        result = (runner or run_cmd)(args, timeout=CODEX_LSOF_TIMEOUT_SECONDS)
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line[1:] for line in result.stdout.splitlines() if line.startswith("n") and len(line) > 1]


def lsof_paths_for_processes(pids: list[int], descriptor: str | None = None, runner: Any = None) -> dict[int, list[str]]:
    unique_pids = sorted({int(pid) for pid in pids if int(pid) > 0})
    if not unique_pids:
        return {}
    args = ["lsof", "-p", ",".join(str(pid) for pid in unique_pids)]
    if descriptor:
        args.extend(["-a", "-d", descriptor])
    args.append("-Fn")
    try:
        result = (runner or run_cmd)(args, timeout=CODEX_LSOF_TIMEOUT_SECONDS)
    except OSError:
        return {pid: [] for pid in unique_pids}
    paths_by_pid: dict[int, list[str]] = {pid: [] for pid in unique_pids}
    if result.returncode != 0:
        return paths_by_pid
    current_pid: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                current_pid = int(line[1:])
            except ValueError:
                current_pid = None
            if current_pid is not None:
                paths_by_pid.setdefault(current_pid, [])
        elif line.startswith("n") and len(line) > 1 and current_pid is not None:
            paths_by_pid.setdefault(current_pid, []).append(line[1:])
    return paths_by_pid


def codex_lsof_cache_key_pid(pid: int) -> str:
    return str(pid)


def codex_transcript_from_lsof(pid: int, root: Path, runner: Any = None) -> Path | None:
    cache_pid = codex_lsof_cache_key_pid(pid)
    cached = cached_transcript_lookup("codex-lsof-pid", root, cache_pid)
    if cached is not _CACHE_MISS:
        return cached
    candidates = codex_rollout_paths(
        lsof_paths_for_process(pid, descriptor=CODEX_LSOF_TRANSCRIPT_DESCRIPTOR_FILTER, runner=runner),
        root,
    )
    return set_cached_transcript_lookup(
        "codex-lsof-pid",
        root,
        cache_pid,
        newest_codex_transcript(candidates),
        ttl=CODEX_LSOF_CACHE_SECONDS,
    )


def prime_codex_transcript_lsof_cache(pids: list[int], root: Path | None = None, runner: Any = None) -> None:
    root = root or Path.home() / ".codex" / "sessions"
    missing = [
        pid
        for pid in sorted({int(pid) for pid in pids if int(pid) > 0})
        if cached_transcript_lookup("codex-lsof-pid", root, codex_lsof_cache_key_pid(pid)) is _CACHE_MISS
    ]
    if not missing:
        return
    paths_by_pid = lsof_paths_for_processes(missing, descriptor=CODEX_LSOF_TRANSCRIPT_DESCRIPTOR_FILTER, runner=runner)
    for pid in missing:
        candidates = codex_rollout_paths(paths_by_pid.get(pid, []), root)
        set_cached_transcript_lookup(
            "codex-lsof-pid",
            root,
            codex_lsof_cache_key_pid(pid),
            newest_codex_transcript(candidates),
            ttl=CODEX_LSOF_CACHE_SECONDS,
        )


def codex_transcript_from_process_fd(
    pid: int,
    root: Path | None = None,
    fd_dir: Path | None = None,
    lsof_runner: Any = None,
) -> Path | None:
    root = root or Path.home() / ".codex" / "sessions"
    if fd_dir is None and platform.system() == "Darwin":
        return codex_transcript_from_lsof(pid, root, runner=lsof_runner)
    fd_dir = fd_dir or Path(f"/proc/{pid}/fd")
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return codex_transcript_from_lsof(pid, root, runner=lsof_runner)
    targets: list[str] = []
    for entry in entries:
        try:
            targets.append(os.readlink(entry))
        except OSError:
            continue
    return newest_codex_transcript(codex_rollout_paths(targets, root))


def read_codex_agent(session: str, pane: TmuxPaneInfo, process: ProcessInfo) -> AgentInfo:
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


def selected_codex_process(processes: list[ProcessInfo]) -> ProcessInfo | None:
    candidates = [process for process in processes if classify_agent(process.command) == "codex"]
    if not candidates:
        return None
    # The Node launcher and native Codex binary are one interactive client. Prefer the native
    # process because it owns the rollout file; fall back to the wrapper for mocks/other clients.
    return next(
        (candidate for candidate in candidates if command_basename(candidate.command) == "codex"),
        candidates[0],
    )


def select_codex_agent(session: str, pane: TmuxPaneInfo, processes: list[ProcessInfo]) -> AgentInfo | None:
    process = selected_codex_process(processes)
    if process is None:
        return None
    return read_codex_agent(session, pane, process)


def select_pane_agent(session: str, pane: TmuxPaneInfo, processes: list[ProcessInfo]) -> AgentInfo | None:
    kinds = [kind for process in processes if (kind := classify_agent(process.command)) in {"claude", "codex"}]
    if not kinds:
        return None
    # `processes` is breadth-first from the tmux pane PID. The first real agent is the interactive
    # owner; a nested agent command launched as a tool must not replace its parent pane identity.
    if kinds[0] == "claude":
        return select_claude_agent(session, pane, processes)
    return select_codex_agent(session, pane, processes)


def _darwin_process_cwd(pid: int) -> str | None:
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidinfo.restype = ctypes.c_int
        info = DarwinProcVnodePathInfo()
        size = ctypes.sizeof(info)
        result = libproc.proc_pidinfo(pid, DARWIN_PROC_PIDVNODEPATHINFO, 0, ctypes.byref(info), size)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if result < size:
        return None
    path = bytes(info.pvi_cdir.vip_path).split(b"\0", 1)[0].decode("utf-8", errors="surrogateescape").strip()
    return path or None


def process_cwd(pid: int, lsof_runner: Any = None) -> str | None:
    if platform.system() == "Darwin":
        cwd = _darwin_process_cwd(pid)
        if cwd:
            return cwd
        paths = lsof_paths_for_process(pid, descriptor="cwd", runner=lsof_runner)
        return paths[0] if paths else None
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        paths = lsof_paths_for_process(pid, descriptor="cwd", runner=lsof_runner)
        return paths[0] if paths else None


def pane_sort_key(pane: TmuxPaneInfo) -> tuple[str, int, int]:
    return (pane.session, int(pane.window), int(pane.pane))


def active_window_for_panes(panes: list[TmuxPaneInfo] | list[dict[str, Any]]) -> str | None:
    for pane in panes:
        if isinstance(pane, TmuxPaneInfo):
            if pane.window_active and pane.window not in (None, ""):
                return str(pane.window)
        elif isinstance(pane, dict) and pane.get("window_active") and pane.get("window") not in (None, ""):
            return str(pane.get("window"))
    return None


def preferred_pane(panes: list[TmuxPaneInfo], agents: list[AgentInfo]) -> TmuxPaneInfo | None:
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

    by_session: dict[str, list[TmuxPaneInfo]] = {session: [] for session in sessions}
    for pane in panes:
        if pane.session in by_session:
            by_session[pane.session].append(pane)

    if platform.system() == "Darwin":
        codex_pids: list[int] = []
        for raw_panes in by_session.values():
            for raw_pane in raw_panes:
                candidates = []
                root_process = processes.get(raw_pane.pid)
                if root_process:
                    candidates.append(root_process)
                candidates.extend(descendants(raw_pane.pid, children))
                process = selected_codex_process(candidates)
                if process is not None:
                    codex_pids.append(process.pid)
        prime_codex_transcript_lsof_cache(codex_pids)

    result: dict[str, SessionInfo] = {}
    for session in sessions:
        raw_session_panes = sorted(by_session.get(session, []), key=pane_sort_key)
        session_panes: list[TmuxPaneInfo] = []
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
            agent = select_pane_agent(session, pane, candidates)
            if agent is not None and agent.pid not in seen_pids:
                seen_pids.add(agent.pid)
                agents.append(agent)
        result[session] = SessionInfo(
            session=session,
            panes=session_panes,
            selected_pane=preferred_pane(session_panes, agents),
            agents=agents,
        )
    return result, errors

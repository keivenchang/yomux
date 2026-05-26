from __future__ import annotations

from .common import *
from .workdir import session_workdir


def list_tmux_panes() -> tuple[list[PaneInfo], str | None]:
    fmt = "\t".join(
        [
            "#{session_name}",
            "#{window_index}",
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
        error = (result.stderr or result.stdout or "tmux list-panes failed").strip()
        return [], error

    panes: list[PaneInfo] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 10:
            continue
        session, window, pane, pane_id, path, command, active, window_active, title, pid_text = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        panes.append(
            PaneInfo(
                session=session,
                window=window,
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
        error = (result.stderr or result.stdout or "ps failed").strip()
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
    for path in base_dir.glob(f"**/{session_id}.jsonl"):
        return path
    return None


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
    )


def find_recent_codex_transcript(cwd: str | None) -> Path | None:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return None
    files = sorted(root.glob("**/rollout-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    needle = json.dumps(cwd) if cwd else None
    for path in files[:80]:
        if not needle:
            return path
        try:
            tail = tail_file_lines(path, 300)
        except OSError:
            continue
        if needle in tail:
            return path
    return None


def read_codex_agent(session: str, pane: PaneInfo, process: ProcessInfo) -> AgentInfo:
    proc_cwd = process_cwd(process.pid) or pane.current_path
    transcript_path = find_recent_codex_transcript(proc_cwd)
    return AgentInfo(
        session=session,
        kind="codex",
        pid=process.pid,
        pane_target=pane.target,
        command=process.command,
        cwd=proc_cwd,
        status=None,
        session_id=None,
        transcript=str(transcript_path) if transcript_path else None,
        error=None if transcript_path else "codex transcript not found by cwd",
    )


def process_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def pane_sort_key(pane: PaneInfo) -> tuple[str, int, int]:
    return (pane.session, int(pane.window), int(pane.pane))


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
        session_panes = sorted(by_session.get(session, []), key=pane_sort_key)
        agents: list[AgentInfo] = []
        seen_pids: set[int] = set()
        for pane in session_panes:
            candidates = []
            root_process = processes.get(pane.pid)
            if root_process:
                candidates.append(root_process)
            candidates.extend(descendants(pane.pid, children))
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

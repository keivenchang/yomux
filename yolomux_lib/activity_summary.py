# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Stateful activity summaries for running AI agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import AgentInfo
from .common import SessionInfo
from .common import tail_file_lines
from .common import truncate_text
from .transcripts import compact_transcript_items
from .transcripts import session_transcript_activity_state


def agent_for_summary(info: SessionInfo) -> AgentInfo | None:
    return next((agent for agent in info.agents if agent.transcript), None) or (info.agents[0] if info.agents else None)


def transcript_file_signature(agent: AgentInfo | None) -> dict[str, Any]:
    if not agent or not agent.transcript:
        return {"path": "", "mtime": 0.0, "size": 0}
    path = Path(agent.transcript).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "mtime": 0.0, "size": 0}
    return {"path": str(path), "mtime": stat.st_mtime, "size": stat.st_size}


def activity_signature(info: SessionInfo, project: dict[str, Any], files_payload: dict[str, Any]) -> dict[str, Any]:
    agent = agent_for_summary(info)
    git = project.get("git") if isinstance(project, dict) else None
    file_rows = []
    for item in files_payload.get("files", []) if isinstance(files_payload, dict) else []:
        if not isinstance(item, dict):
            continue
        file_rows.append([
            item.get("status") or "",
            item.get("repo") or "",
            item.get("path") or "",
            item.get("abs_path") or "",
            item.get("added"),
            item.get("removed"),
            item.get("mtime"),
        ])
    return {
        "session": info.session,
        "agent": agent.kind if agent else "",
        "agent_model": agent.model if agent else "",
        "agent_status": agent.status if agent else "",
        "transcript": transcript_file_signature(agent),
        "git": {
            "root": git.get("root") if isinstance(git, dict) else "",
            "branch": git.get("branch") if isinstance(git, dict) else "",
            "dirty_count": git.get("dirty_count") if isinstance(git, dict) else None,
            "ahead": git.get("ahead") if isinstance(git, dict) else None,
            "behind": git.get("behind") if isinstance(git, dict) else None,
            "head": git.get("head") if isinstance(git, dict) else "",
        },
        "files": file_rows,
    }


def recent_transcript_items(agent: AgentInfo | None, max_lines: int = 800) -> list[dict[str, str]]:
    if not agent or not agent.transcript:
        return []
    try:
        text = tail_file_lines(Path(agent.transcript).expanduser(), max_lines)
    except OSError:
        return []
    return compact_transcript_items(text, 80)


def latest_item_text(items: list[dict[str, str]], role: str) -> str:
    for item in reversed(items):
        if item.get("role") != role:
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text:
            return truncate_text(text, 180)
    return ""


def changed_file_totals(files_payload: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in files_payload.get("files", []) if isinstance(item, dict)] if isinstance(files_payload, dict) else []
    added = sum(int(item.get("added") or 0) for item in files if isinstance(item.get("added") or 0, int))
    removed = sum(int(item.get("removed") or 0) for item in files if isinstance(item.get("removed") or 0, int))
    return {"count": len(files), "added": added, "removed": removed}


def file_label(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "M")
    path = str(item.get("path") or item.get("abs_path") or "")
    added = item.get("added")
    removed = item.get("removed")
    diff = ""
    if isinstance(added, int) or isinstance(removed, int):
        diff = f" (+{int(added or 0)}/-{int(removed or 0)})"
    return f"{status} {path}{diff}".strip()


def changed_file_lines(files_payload: dict[str, Any], limit: int = 6) -> list[str]:
    files = [item for item in files_payload.get("files", []) if isinstance(item, dict)] if isinstance(files_payload, dict) else []
    lines = [file_label(item) for item in files[:limit]]
    extra = len(files) - limit
    if extra > 0:
        lines.append(f"+{extra} more")
    return lines


def plural(count: int, singular: str, plural_text: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural_text or singular + 's')}"


def human_join(items: list[str]) -> str:
    values = [item for item in items if item]
    if len(values) <= 1:
        return values[0] if values else ""
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def agent_label(agent_name: str) -> str:
    return {
        "codex": "Codex",
        "claude": "Claude",
        "term": "Terminal",
        "terminal": "Terminal",
    }.get(agent_name.lower(), agent_name or "No agent")


def agent_display_label(agent: AgentInfo | None, fallback_kind: str = "") -> str:
    label = agent_label(agent.kind if agent else fallback_kind)
    model = (agent.model or "").strip() if agent else ""
    if model:
        return f"{label} {model}"
    return label


def repo_label(repo: str) -> str:
    return Path(repo).name if repo else "no repo"


def files_sentence(files: dict[str, Any]) -> str:
    count = int(files.get("count") or 0)
    added = int(files.get("added") or 0)
    removed = int(files.get("removed") or 0)
    if not count:
        return "no modified files attributed yet"
    return f"{plural(count, 'file')} changed (+{added}/-{removed})"


def work_sentence(work: str, goal: str) -> str:
    work_text = truncate_text(work or goal, 180)
    if not work_text:
        return "It has not reported a current task yet."
    if work and goal and work.strip() != goal.strip():
        return f"It has worked on {work_text} for {truncate_text(goal, 120)}."
    return f"It has worked on {work_text}."


def project_status_sentence(project: dict[str, Any], files: dict[str, Any], active: bool) -> str:
    parts: list[str] = []
    git = project.get("git") if isinstance(project, dict) else None
    pr = project.get("pull_request") if isinstance(project, dict) else None
    ci = ci_summary(project)
    if ci:
        parts.append(ci)
    if isinstance(git, dict):
        dirty_count = git.get("dirty_count")
        ahead = git.get("ahead")
        behind = git.get("behind")
        if isinstance(dirty_count, int) and dirty_count > 0:
            parts.append(f"{plural(dirty_count, 'dirty file')}")
        if isinstance(ahead, int) and ahead > 0:
            parts.append(f"{plural(ahead, 'commit')} ahead")
        if isinstance(behind, int) and behind > 0:
            parts.append(f"{plural(behind, 'commit')} behind")
    if isinstance(pr, dict) and pr.get("url"):
        number = pr.get("number")
        parts.append(f"PR #{number}" if number else "PR linked")
    if int(files.get("count") or 0) and not parts:
        parts.append("ready for review or commit")
    if not parts:
        parts.append("still active" if active else "waiting for more activity")
    return "; ".join(parts)


def repo_names(project: dict[str, Any], files_payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    git = project.get("git") if isinstance(project, dict) else None
    if isinstance(git, dict) and git.get("root"):
        names.append(str(git["root"]))
    for item in files_payload.get("repos", []) if isinstance(files_payload, dict) else []:
        repo = item.get("repo") if isinstance(item, dict) else None
        if isinstance(repo, str) and repo and repo not in names:
            names.append(repo)
    return names


def ci_summary(project: dict[str, Any]) -> str:
    pr = project.get("pull_request") if isinstance(project, dict) else None
    if not isinstance(pr, dict):
        return ""
    checks = pr.get("checks")
    if isinstance(checks, dict):
        summary = checks.get("summary")
        state = checks.get("state")
        if isinstance(summary, str) and summary:
            return summary
        if isinstance(state, str) and state and state != "unknown":
            return f"CI {state}"
    state = pr.get("state")
    if isinstance(state, str) and state:
        return f"PR {state}"
    return ""


def build_session_activity_summary(info: SessionInfo, project: dict[str, Any], files_payload: dict[str, Any]) -> dict[str, Any]:
    agent = agent_for_summary(info)
    items = recent_transcript_items(agent)
    activity_state = session_transcript_activity_state(info)
    git = project.get("git") if isinstance(project, dict) else None
    pr = project.get("pull_request") if isinstance(project, dict) else None
    goal = latest_item_text(items, "user")
    latest_tool = latest_item_text(items, "tool_use")
    latest_assistant = latest_item_text(items, "assistant")
    work = ""
    if isinstance(pr, dict):
        work = str(pr.get("title") or pr.get("description") or "")
    if not work and isinstance(git, dict):
        work = str(git.get("subject") or git.get("branch") or "")
    if not work:
        work = latest_assistant
    files = changed_file_totals(files_payload)
    file_lines = changed_file_lines(files_payload)
    repos = repo_names(project, files_payload)
    agent_name = agent.kind if agent else "no agent"
    state_label = "active" if activity_state.get("key") == "working" else "idle"
    ci = ci_summary(project)
    status = project_status_sentence(project, files, activity_state.get("key") == "working")
    local_parts = [
        f"{agent_display_label(agent)} session {info.session} is {state_label}.",
        work_sentence(work, goal),
        f"The changes are {files_sentence(files)}.",
        f"Status: {status}.",
    ]
    local = " ".join(local_parts)
    lines = [local]
    if repos:
        branch = f" @ {git.get('branch')}" if isinstance(git, dict) and git.get("branch") else ""
        lines.append(f"repo: {repo_label(repos[0])}{branch}")
    if goal:
        lines.append(f"goal: {goal}")
    if work:
        lines.append(f"work: {truncate_text(work, 180)}")
    if latest_tool:
        lines.append(f"latest tool: {latest_tool}")
    if ci:
        lines.append(ci)
    if files["count"]:
        lines.append(f"files: {files['count']} changed (+{files['added']}/-{files['removed']})")
    return {
        "session": info.session,
        "agent": agent_name,
        "agent_model": agent.model if agent else "",
        "agent_label": agent_display_label(agent, agent_name),
        "agent_status": agent.status if agent else "",
        "active": activity_state.get("key") == "working",
        "state": activity_state,
        "repos": repos,
        "goal": goal,
        "work": truncate_text(work, 220) if work else "",
        "ci": ci,
        "status_text": status,
        "files": files,
        "file_lines": file_lines,
        "lines": lines,
        "local": local,
    }


def build_global_activity_summary(session_summaries: list[dict[str, Any]], errors: list[str] | None = None) -> dict[str, Any]:
    active = [item for item in session_summaries if item.get("active")]
    file_count = sum(int(item.get("files", {}).get("count") or 0) for item in session_summaries)
    added = sum(int(item.get("files", {}).get("added") or 0) for item in session_summaries)
    removed = sum(int(item.get("files", {}).get("removed") or 0) for item in session_summaries)
    total = len(session_summaries)
    topics: list[str] = []
    repos: list[str] = []
    for item in session_summaries:
        topic = truncate_text(str(item.get("work") or item.get("goal") or ""), 90)
        if topic and topic not in topics:
            topics.append(topic)
        for repo in item.get("repos") or []:
            label = repo_label(str(repo))
            if label and label not in repos:
                repos.append(label)
    if total:
        work_text = human_join(topics[:3]) if topics else "the active tmux sessions"
        repo_text = human_join(repos[:3]) if repos else "the current workspace"
        agent_word = "agent" if total == 1 else "agents"
        active_text = f"{len(active)} of {total} AI {agent_word} {'is' if len(active) == 1 else 'are'} active"
        headline = f"You've worked on {work_text}. The changes are {files_sentence({'count': file_count, 'added': added, 'removed': removed})} across {repo_text}; {active_text}."
    else:
        headline = "No AI agent activity is available yet."
    lines = [headline]
    for item in session_summaries:
        status = "active" if item.get("active") else "idle"
        repos = item.get("repos") or []
        repo = repo_label(str(repos[0])) if repos else "no repo"
        files = item.get("files", {})
        work = item.get("work") or item.get("goal") or item.get("ci") or ""
        agent_text = str(item.get("agent_label") or agent_label(str(item.get("agent") or "")))
        status_text = str(item.get("status_text") or status)
        lines.append(f"Session {item.get('session')}: {agent_text} is {status} in {repo}; {files_sentence(files)}; {status_text}; {truncate_text(str(work), 150)}")
    for error in errors or []:
        lines.append(f"Activity summary error: {error}")
    return {
        "active_agents": len(active),
        "total_agents": len(session_summaries),
        "files": {"count": file_count, "added": added, "removed": removed},
        "headline": headline,
        "lines": lines,
    }

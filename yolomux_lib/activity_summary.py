# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Stateful activity summaries for running AI agents."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from .common import AgentInfo
from .common import SessionInfo
from .common import tail_file_lines
from .common import truncate_text
from .transcripts import compact_transcript_items
from .transcripts import newest_transcript_timestamp
from .transcripts import session_transcript_activity_state


ACTIVITY_SUMMARY_FORMAT_VERSION = 4
YOAGENT_CONTEXT_GUARD = (
    "Use only the supplied YOLOmux concepts and activity context. Do not run tools, inspect files, "
    "or enumerate ~/.claude, ~/.codex, transcript directories, or any other filesystem path. "
    "If a timestamp or path is not present in the context, say that it is not available."
)
YOAGENT_README_PATH = Path(__file__).resolve().parents[1] / "README.md"
YOAGENT_HELP_PRIMER_MAX_CHARS = 8_000
_YOAGENT_HELP_PRIMER_CACHE: str | None = None
YOAGENT_CONTEXT_CHAIN_PRIMER = (
    "Context sourcing chain: YOLOmux starts from tmux sessions. Each tmux session is a YOLOmux Tab. "
    "YOLOmux detects Claude/Codex agents running in that session, reads the agent's session transcript JSONL when one exists, "
    "combines that with git metadata and changed-file summaries, and turns that into YO!agent insights. "
    "If a session has no detected agent or no transcript, YO!agent may know the tmux session exists but will have little or no activity insight for it."
)


def markdown_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start = -1
    marker = f"## {heading}"
    for index, line in enumerate(lines):
        if line.strip() == marker:
            start = index + 1
            break
    if start < 0:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def compact_markdown_for_prompt(markdown: str, max_chars: int) -> str:
    lines: list[str] = []
    in_code = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line:
            continue
        lines.append(line)
    text = "\n".join(lines)
    return truncate_text(text, max_chars)


def yolomux_help_primer() -> str:
    global _YOAGENT_HELP_PRIMER_CACHE
    if _YOAGENT_HELP_PRIMER_CACHE is not None:
        return _YOAGENT_HELP_PRIMER_CACHE
    try:
        readme = YOAGENT_README_PATH.read_text(encoding="utf-8")
    except OSError:
        readme = ""
    sections = [
        markdown_section(readme, "Concepts"),
        markdown_section(readme, "Daily use"),
        markdown_section(readme, "UI features"),
        markdown_section(readme, "Files and editors"),
    ]
    body = "\n".join(section for section in sections if section)
    primer = compact_markdown_for_prompt(body, YOAGENT_HELP_PRIMER_MAX_CHARS)
    if not primer:
        primer = (
            "Pane: a visible YOLOmux split region. Tab: one item inside a pane, such as a tmux session, Finder/File Explorer, File, Preferences, Changes, or YO!agent. "
            "A tmux session tab has its own tmux windows and tmux panes. Finder and File Explorer are the same tab with platform-specific naming. "
            "YO!agent insights come from detected Claude/Codex agents, their session transcripts, git metadata, and changed-file summaries."
        )
    _YOAGENT_HELP_PRIMER_CACHE = "YOLOmux help primer from README.md:\n" + primer + "\n" + YOAGENT_CONTEXT_CHAIN_PRIMER
    return _YOAGENT_HELP_PRIMER_CACHE


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


def relative_age_text(timestamp: float | None, now: datetime | None = None) -> str:
    if not timestamp:
        return ""
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int(current.timestamp() - timestamp))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = max(1, round(seconds / 3600))
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = max(1, round(seconds / 86400))
    return f"{days} day{'s' if days != 1 else ''} ago"


def transcript_last_activity(agent: AgentInfo | None) -> dict[str, Any]:
    if not agent or not agent.transcript:
        return {"timestamp": None, "text": "", "path": ""}
    path = Path(agent.transcript).expanduser()
    timestamps: list[datetime] = []
    try:
        timestamps.append(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
    except OSError:
        pass
    try:
        newest = newest_transcript_timestamp(tail_file_lines(path, 800))
    except OSError:
        newest = None
    if newest:
        timestamps.append(newest)
    if not timestamps:
        return {"timestamp": None, "text": "", "path": str(path)}
    latest = max(timestamps)
    return {"timestamp": latest.timestamp(), "text": relative_age_text(latest.timestamp()), "path": str(path)}


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
        "summary_format": ACTIVITY_SUMMARY_FORMAT_VERSION,
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


def repo_sentence(repos: list[str]) -> str:
    labels = [repo_label(repo) for repo in repos[:3] if repo]
    if not labels:
        return "no detected repo"
    return human_join(labels)


def work_sentence(work: str, goal: str) -> str:
    work_text = truncate_text(work or goal, 180)
    if not work_text:
        return "It has not said what it is working on yet."
    if work and goal and work.strip() != goal.strip():
        return f"It has been working on {work_text} for {truncate_text(goal, 120)}."
    return f"It has been working on {work_text}."


def unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = truncate_text(str(value or "").strip(), 120)
        if text and text not in result:
            result.append(text)
    return result


def human_status_headline(topics: list[str], repos: list[str], files: dict[str, Any], active_count: int, total_count: int) -> str:
    if total_count <= 0:
        return "No AI agent activity is available yet."
    work_items = unique_nonempty(topics)
    repo_items = unique_nonempty([repo_label(repo) for repo in repos])
    most_recent = work_items[0] if work_items else "the current agent work"
    target = human_join(repo_items[:3]) if repo_items else "the current workspace"
    purpose = f"finish {most_recent}" if work_items else "make progress on the current task"
    agent_word = "agent" if total_count == 1 else "agents"
    active_text = f"{active_count} of {total_count} AI {agent_word} {'is' if active_count == 1 else 'are'} active"
    sentences = [
        f"Your most recent work is about {most_recent}, and you are currently making changes to {target} in order to {purpose}.",
    ]
    other_work = human_join(work_items[1:4])
    if other_work:
        sentences.append(f"Other work includes {other_work}.")
    sentences.append(f"So far: {files_sentence(files)}; {active_text}.")
    return " ".join(sentences)


def summary_last_activity_ts(summary: dict[str, Any]) -> float:
    value = summary.get("last_activity_ts")
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    return timestamp if timestamp > 0 else 0.0


def summary_work_label(summary: dict[str, Any]) -> str:
    return truncate_text(str(summary.get("work") or summary.get("goal") or summary.get("ci") or "this work"), 90)


def freshest_summary(session_summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [item for item in session_summaries if summary_last_activity_ts(item)]
    if not candidates:
        return session_summaries[0] if session_summaries else None
    return max(candidates, key=summary_last_activity_ts)


def stale_summary(session_summaries: list[dict[str, Any]], stale_after_seconds: int = 7 * 24 * 3600) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc).timestamp()
    candidates = [
        item for item in session_summaries
        if summary_last_activity_ts(item) and now - summary_last_activity_ts(item) >= stale_after_seconds
    ]
    if not candidates:
        return None
    return min(candidates, key=summary_last_activity_ts)


def global_recommendation_sentence(session_summaries: list[dict[str, Any]]) -> str:
    if not session_summaries:
        return ""
    active = [item for item in session_summaries if item.get("active")]
    target = active[0] if active else freshest_summary(session_summaries)
    if not target:
        return ""
    session = target.get("session")
    work = summary_work_label(target)
    last = str(target.get("last_activity_text") or "").strip()
    recency = f", last worked {last}" if last else ""
    if active:
        return f"Recommendation: keep session {session} focused on {work} until it reaches a clean stopping point{recency}."
    return f"Recommendation: resume session {session} first because it has the freshest context for {work}{recency}."


def stale_work_sentence(session_summaries: list[dict[str, Any]]) -> str:
    stale = stale_summary(session_summaries)
    if not stale:
        return ""
    session = stale.get("session")
    work = summary_work_label(stale)
    last = str(stale.get("last_activity_text") or "").strip()
    return f"You have not touched session {session} ({work}) for {last}; ask it to summarize before resuming, or close it if the work is no longer useful."


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
    last_activity = transcript_last_activity(agent)
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
    recent_files = f" Recent files: {', '.join(file_lines[:3])}." if file_lines else ""
    local_parts = [
        f"{agent_display_label(agent)} session {info.session} is {state_label} in {repo_sentence(repos)}.",
        f"You last worked on this session {last_activity['text']}." if last_activity.get("text") else "",
        work_sentence(work, goal),
        f"It currently has {files_sentence(files)}.{recent_files}",
        f"Status check: {status}.",
    ]
    local = " ".join(part for part in local_parts if part)
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
        "last_activity": last_activity,
        "last_activity_text": last_activity.get("text") or "",
        "last_activity_ts": last_activity.get("timestamp"),
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
        headline = human_status_headline(topics, repos, {"count": file_count, "added": added, "removed": removed}, len(active), total)
    else:
        headline = "No AI agent activity is available yet."
    lines = [headline]
    for global_line in (global_recommendation_sentence(session_summaries), stale_work_sentence(session_summaries)):
        if global_line:
            lines.append(global_line)
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


def yoagent_context_lines(activity_payload: dict[str, Any]) -> list[str]:
    global_summary = activity_payload.get("global") if isinstance(activity_payload, dict) else {}
    sessions = activity_payload.get("sessions") if isinstance(activity_payload, dict) else {}
    lines: list[str] = []
    if isinstance(global_summary, dict):
        headline = str(global_summary.get("headline") or "").strip()
        if headline:
            lines.append(f"global: {headline}")
        for line in global_summary.get("lines") or []:
            text = str(line or "").strip()
            if text and text != headline:
                lines.append(f"global detail: {text}")
    if isinstance(sessions, dict):
        for session, summary in sessions.items():
            if not isinstance(summary, dict):
                continue
            agent = str(summary.get("agent_label") or agent_label(str(summary.get("agent") or "")))
            repos = ", ".join(repo_label(str(repo)) for repo in summary.get("repos") or []) or "no repo"
            files = files_sentence(summary.get("files") or {})
            work = str(summary.get("work") or summary.get("goal") or "").strip()
            status = str(summary.get("status_text") or "").strip()
            state = "active" if summary.get("active") else "idle"
            parts = [f"session {session}: {agent} is {state}", f"repos: {repos}", f"changes: {files}"]
            last_activity = str(summary.get("last_activity_text") or "").strip()
            if last_activity:
                parts.append(f"last worked: {last_activity}")
            if work:
                parts.append(f"work: {truncate_text(work, 180)}")
            if status:
                parts.append(f"status: {truncate_text(status, 160)}")
            file_lines = [str(item) for item in summary.get("file_lines") or [] if item]
            if file_lines:
                parts.append(f"files: {', '.join(file_lines[:6])}")
            lines.append("; ".join(parts))
    errors = activity_payload.get("errors") if isinstance(activity_payload, dict) else []
    for error in errors or []:
        lines.append(f"error: {error}")
    return lines


YOAGENT_HISTORY_TURN_LIMIT = 4


def build_yoagent_chat_prompt(question: str, activity_payload: dict[str, Any], settings: dict[str, Any], history: list[dict[str, str]] | None = None) -> str:
    system_prompt = str(settings.get("system_prompt") or "You are YO!agent. Help users operate YOLOmux using the concepts below and report on the supplied activity context.")
    intro = str(settings.get("intro") or "Summarize the running AI agents and changed files.")
    output_format = str(settings.get("format") or "Keep answers short and factual.")
    history_lines = []
    for item in (history or [])[-YOAGENT_HISTORY_TURN_LIMIT:]:
        role = "user" if item.get("role") == "user" else "assistant"
        content = truncate_text(" ".join(str(item.get("content") or "").split()), 600)
        if content:
            history_lines.append(f"{role}: {content}")
    context = "\n".join(yoagent_context_lines(activity_payload)) or "No AI agent activity is available."
    return "\n\n".join([
        system_prompt,
        intro,
        output_format,
        YOAGENT_CONTEXT_GUARD,
        "YOLOmux concepts:",
        yolomux_help_primer(),
        "Activity context:",
        context,
        "Recent chat:",
        "\n".join(history_lines) if history_lines else "(none)",
        f"User question: {question}",
    ])


def build_yoagent_resume_prompt(question: str, activity_payload: dict[str, Any], settings: dict[str, Any], context_changed: bool) -> str:
    output_format = str(settings.get("format") or "Keep answers short and factual.")
    if context_changed:
        context = "\n".join(yoagent_context_lines(activity_payload)) or "No AI agent activity is available."
        context_block = "Activity summary changed since the previous YO!agent turn. Use this current summarized state:\n" + context
    else:
        context_block = "Activity summary is unchanged since the previous YO!agent turn. Reuse the prior context in this resumed conversation."
    return "\n\n".join([
        "Continue the existing YO!agent conversation.",
        output_format,
        YOAGENT_CONTEXT_GUARD,
        "YOLOmux concepts:",
        yolomux_help_primer(),
        context_block,
        f"User question: {question}",
    ])


def deterministic_yoagent_help_reply(question: str) -> str:
    text = str(question or "").lower()
    if not text:
        return ""
    wants_context = any(phrase in text for phrase in ["where do your insights", "where does your insight", "where do you get", "context come", "context comes", "no summary", "no insight", "no transcript", "session 7"])
    if wants_context:
        return YOAGENT_CONTEXT_CHAIN_PRIMER
    mentions_pane = "pane" in text
    mentions_window = "window" in text
    mentions_tab = "tab" in text
    mentions_finder = "finder" in text or "file explorer" in text
    mentions_split = "split" in text or "drag" in text
    if mentions_pane and ("what" in text or "difference" in text or "mean" in text):
        return "A YOLOmux Pane is a visible browser split region. It can hold multiple Tabs but shows one Tab at a time. A tmux pane is different: it is a split inside a tmux window, inside one tmux session tab."
    if mentions_window and ("tmux" in text or mentions_tab or "difference" in text):
        return "In YOLOmux, window means a tmux window. A YOLOmux Tab can be a tmux session, and that tmux session has its own tmux windows and tmux panes. YOLOmux itself is organized as Panes and Tabs, not windows."
    if mentions_tab and ("what" in text or "difference" in text or "mean" in text):
        return "A YOLOmux Tab is an item inside a Pane: a tmux session, Finder/File Explorer, a File editor/viewer, Preferences, Changes, or YO!agent. Tabs can be active, minimized in a pane's tab strip, or inactive."
    if mentions_finder:
        return "Finder and File Explorer are the same YOLOmux tab; the name changes by platform. Open it from File -> Finder/File Explorer. Single-click selects files, double-click opens files or makes a directory the root."
    if mentions_split:
        return "Drag a tab onto another pane to move it there, or drop near an edge to split when there is enough room. Dropping in the middle adds the tab to that pane's tab strip."
    return ""


def deterministic_yoagent_reply(question: str, activity_payload: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    help_reply = deterministic_yoagent_help_reply(question)
    if help_reply:
        return help_reply
    global_summary = activity_payload.get("global") if isinstance(activity_payload, dict) else {}
    sessions = activity_payload.get("sessions") if isinstance(activity_payload, dict) else {}
    headline = str(global_summary.get("headline") or "No AI agent activity is available yet.") if isinstance(global_summary, dict) else "No AI agent activity is available yet."
    question_text = str(question or "").lower()
    selected_sessions: list[dict[str, Any]] = []
    if isinstance(sessions, dict):
        for session, summary in sessions.items():
            if not isinstance(summary, dict):
                continue
            if session and session in question_text:
                selected_sessions.append(summary)
    sentences = [headline]
    if isinstance(global_summary, dict):
        for line in global_summary.get("lines") or []:
            text = str(line or "").strip()
            if text and text != headline:
                sentences.append(text)
    for summary in selected_sessions:
        local = str(summary.get("local") or "").strip()
        if local:
            sentences.append(f"Details: {local}")
    return " ".join(sentences)

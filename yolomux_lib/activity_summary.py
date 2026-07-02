# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Stateful activity summaries for running AI agents."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from .common import AgentInfo
from .common import SessionInfo
from .common import is_generated_upload_name
from .common import project_git
from .common import tail_file_lines
from .common import truncate_text
from .transcripts import compact_transcript_items
from .transcripts import newest_transcript_activity_timestamp
from .transcripts import session_transcript_activity_state
from .transcripts import transcript_activity_state
from .web import server_plural
from .web import server_string
from .settings import DEFAULT_SETTINGS
from .settings import LEGACY_YOAGENT_DEFAULTS
from .yoagent.preferences import product_capability_registry


ACTIVITY_SUMMARY_FORMAT_VERSION = 4
RECENT_ACTIVITY_SECONDS = 5 * 60
_YOAGENT_HELP_PRIMER_CACHE: dict[str, str] = {}
YOAGENT_DEFAULT_TEXT_KEYS = {
    "system_prompt": "yoagent.prompt.system",
    "intro": "yoagent.prompt.intro",
    "format": "yoagent.prompt.format",
}


def localized_list(locale: str, items: list[str]) -> str:
    values = [item for item in items if item]
    if len(values) <= 1:
        return values[0] if values else ""
    if len(values) == 2:
        return server_string(locale, "summary.list.two", first=values[0], second=values[1])
    return server_string(locale, "summary.list.more", head=", ".join(values[:-1]), last=values[-1])


def active_state_label(active: bool, locale: str = "en") -> str:
    return server_string(locale, "summary.state.active" if active else "summary.state.idle")


def summary_active_state_label(summary: dict[str, Any], locale: str = "en") -> str:
    return str(summary.get("activity_label") or active_state_label(bool(summary.get("active")), locale))


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


def yolomux_help_primer(locale: str = "en") -> str:
    if locale not in _YOAGENT_HELP_PRIMER_CACHE:
        _YOAGENT_HELP_PRIMER_CACHE[locale] = "\n".join([
            server_string(locale, "yoagent.help.primer"),
            server_string(locale, "yoagent.help.contextSource"),
        ])
    return _YOAGENT_HELP_PRIMER_CACHE[locale]


def agent_for_summary(info: SessionInfo) -> AgentInfo | None:
    return next((agent for agent in info.agents if agent.transcript), None) or (info.agents[0] if info.agents else None)


def transcript_file_signature(agent: AgentInfo | None) -> dict[str, Any]:
    if not agent or not agent.transcript:
        return {"path": "", "mtime": 0.0, "mtime_ns": 0, "size": 0}
    path = Path(agent.transcript).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "mtime": 0.0, "mtime_ns": 0, "size": 0}
    return {"path": str(path), "mtime": stat.st_mtime, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def relative_age_text(timestamp: float | None, now: datetime | None = None, locale: str = "en") -> str:
    if not timestamp:
        return ""
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int(current.timestamp() - timestamp))
    if seconds < 90:
        return server_string(locale, "summary.relative.justNow")
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return server_plural(locale, "summary.relative.minute", minutes)
    if seconds < 86400:
        hours = max(1, round(seconds / 3600))
        return server_plural(locale, "summary.relative.hour", hours)
    days = max(1, round(seconds / 86400))
    return server_plural(locale, "summary.relative.day", days)


def compact_relative_age_text(timestamp: float | None, now: datetime | None = None, locale: str = "en") -> str:
    if not timestamp:
        return ""
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int(current.timestamp() - timestamp))
    if seconds < 90:
        return server_string(locale, "summary.relative.justNow")
    if seconds < 3600:
        return server_plural(locale, "relative.compact.minute", max(1, round(seconds / 60)))
    if seconds < 86400:
        return server_plural(locale, "relative.compact.hour", max(1, round(seconds / 3600)))
    return server_plural(locale, "relative.compact.day", max(1, round(seconds / 86400)))


def agent_transcript_activity_ts(agent: AgentInfo | None) -> dict[str, Any]:
    if not agent or not agent.transcript:
        return {"timestamp": None, "source": "none", "reason": "no transcript", "path": ""}
    path = Path(agent.transcript).expanduser()
    mtime: datetime | None = None
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError as exc:
        stat_error = str(exc)
    else:
        stat_error = ""
    try:
        newest = newest_transcript_activity_timestamp(tail_file_lines(path, 800), str(agent.kind or "").lower())
    except OSError as exc:
        newest = None
        read_error = str(exc)
    else:
        read_error = ""
    if newest is not None:
        return {"timestamp": newest.timestamp(), "source": "transcript-event", "reason": "meaningful transcript event", "path": str(path)}
    if mtime is not None:
        return {"timestamp": mtime.timestamp(), "source": "file-mtime", "reason": "file mtime fallback: no meaningful transcript timestamp", "path": str(path)}
    reason = read_error or stat_error or "no transcript timestamp"
    return {"timestamp": None, "source": "none", "reason": reason, "path": str(path)}


def transcript_last_activity(agent: AgentInfo | None, locale: str = "en") -> dict[str, Any]:
    activity = dict(agent_transcript_activity_ts(agent))
    timestamp = activity.get("timestamp")
    activity["text"] = relative_age_text(timestamp, locale=locale) if isinstance(timestamp, (int, float)) and timestamp > 0 else ""
    return activity


def recent_activity_label(activity_state: dict[str, Any], last_activity: dict[str, Any], now: datetime | None = None, locale: str = "en") -> tuple[str, bool]:
    if activity_state.get("key") == "working":
        return active_state_label(True, locale), True
    timestamp = last_activity.get("timestamp") if isinstance(last_activity, dict) else None
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        current = now or datetime.now(timezone.utc)
        if current.timestamp() - timestamp <= RECENT_ACTIVITY_SECONDS:
            return server_string(locale, "summary.state.recent"), True
    return active_state_label(False, locale), False


def agent_window_for_summary(info: SessionInfo, agent: AgentInfo) -> tuple[str, int | None, str]:
    for pane in info.panes:
        if pane.target == agent.pane_target:
            window = str(pane.window or "")
            try:
                return window, int(window), str(pane.pane or "")
            except ValueError:
                return window, None, str(pane.pane or "")
    match = re.match(r"^[^:]+:(?P<window>[^.]+)(?:\.(?P<pane>.*))?$", str(agent.pane_target or ""))
    if not match:
        return "", None, ""
    window = match.group("window") or ""
    try:
        return window, int(window), match.group("pane") or ""
    except ValueError:
        return window, None, match.group("pane") or ""


def agent_window_name_for_summary(info: SessionInfo, agent: AgentInfo, window: str) -> str:
    for pane in info.panes:
        if pane.target == agent.pane_target:
            return str(pane.window_name or pane.process_label or agent.command or agent.kind or "").strip()
    for pane in info.panes:
        if str(pane.window or "") == str(window or ""):
            return str(pane.window_name or pane.process_label or agent.command or agent.kind or "").strip()
    return str(agent.command or agent.kind or "").strip()


def session_file_matches_agent_window(item: dict[str, Any], agent: AgentInfo | None, window: str = "") -> bool:
    windows = item.get("agent_windows")
    if not isinstance(windows, list) or not windows:
        return True
    agent_target = str(agent.pane_target or "") if agent else ""
    agent_kind = str(agent.kind or "").lower() if agent else ""
    window_text = str(window or "")
    for raw in windows:
        if not isinstance(raw, dict):
            continue
        if agent_target and str(raw.get("pane_target") or "") == agent_target:
            return True
        if window_text and str(raw.get("window") or "") == window_text and str(raw.get("kind") or "").lower() == agent_kind:
            return True
    return False


def recent_agent_paths_from_files(files_payload: dict[str, Any] | None, limit: int = 3, agent: AgentInfo | None = None, window: str = "") -> list[dict[str, Any]]:
    if not isinstance(files_payload, dict):
        return []
    by_path: dict[str, dict[str, Any]] = {}
    files = [item for item in files_payload.get("files", []) if isinstance(item, dict)]
    for item in files:
        if not session_file_matches_agent_window(item, agent, window):
            continue
        raw_repo = str(item.get("repo") or "").strip()
        raw_abs_path = str(item.get("abs_path") or "").strip()
        if raw_abs_path and is_generated_upload_name(raw_abs_path):
            continue
        path = raw_repo
        if not path:
            continue
        existing = by_path.get(path) or {"path": path, "count": 0, "mtime": 0.0, "statuses": []}
        existing["count"] = int(existing.get("count") or 0) + 1
        existing["mtime"] = max(float(existing.get("mtime") or 0.0), float(item.get("mtime") or 0.0))
        status = str(item.get("status") or "").strip()
        if status and status not in existing["statuses"]:
            existing["statuses"].append(status)
        by_path[path] = existing
    return sorted(by_path.values(), key=lambda item: (-float(item.get("mtime") or 0.0), str(item.get("path") or "")))[:limit]


def build_recent_agents_payload(
    sessions: dict[str, SessionInfo],
    session_order: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
    session_files_by_session: dict[str, dict[str, Any]] | None = None,
    locale: str = "en",
) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    order = {str(session): index for index, session in enumerate(session_order or [])}
    rows: list[dict[str, Any]] = []
    for session, info in sessions.items():
        for agent_index, agent in enumerate(info.agents):
            kind = str(agent.kind or "").lower()
            if kind not in {"claude", "codex"}:
                continue
            window, window_index, pane = agent_window_for_summary(info, agent)
            last_activity = transcript_last_activity(agent, locale)
            activity_state = transcript_activity_state(agent.transcript, kind) if agent.transcript else {"key": "idle", "text": ""}
            running = activity_state.get("key") == "working"
            last_used_ts = last_activity.get("timestamp")
            if not isinstance(last_used_ts, (int, float)):
                last_used_ts = 0.0
            sort_ts = current.timestamp() if running else float(last_used_ts or 0.0)
            window_label = window if window else "?"
            window_name = agent_window_name_for_summary(info, agent, window) or kind
            window_display = f"{window_label}:{window_name}"
            rows.append({
                "session": session,
                "window": window,
                "window_index": window_index,
                "window_name": window_name,
                "window_label": window_display,
                "pane": pane,
                "pane_target": agent.pane_target,
                "agent_kind": kind,
                "agent_model": agent.model or "",
                "cwd": agent.cwd or "",
                "transcript": agent.transcript or "",
                "recent_paths": recent_agent_paths_from_files((session_files_by_session or {}).get(session), agent=agent, window=window),
                "last_used_ts": float(last_used_ts or 0.0),
                "last_used_text": last_activity.get("text") or "",
                "last_used_source": last_activity.get("source") or "",
                "last_used_reason": last_activity.get("reason") or "",
                "running": running,
                "state": activity_state.get("key") or "idle",
                "state_text": activity_state.get("text") or "",
                "sort_ts": sort_ts,
                "label": server_string(locale, "summary.recentAgentLabel", session=session, window=window_display),
                "_session_order": order.get(str(session), len(order)),
                "_agent_order": agent_index,
            })
    rows.sort(key=lambda item: (-float(item.get("sort_ts") or 0.0), int(item.get("_session_order") or 0), str(item.get("window") or ""), int(item.get("_agent_order") or 0)))
    for item in rows:
        item.pop("_session_order", None)
        item.pop("_agent_order", None)
    return rows


def activity_signature(info: SessionInfo, project: dict[str, Any], files_payload: dict[str, Any]) -> dict[str, Any]:
    agent = agent_for_summary(info)
    git = project_git(project)
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


def _coerce_count(value: Any) -> int:
    # count numeric strings ("5" -> 5) but never a bool (added=True must NOT count as +1).
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def changed_file_totals(files_payload: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in files_payload.get("files", []) if isinstance(item, dict)] if isinstance(files_payload, dict) else []
    added = sum(_coerce_count(item.get("added")) for item in files)
    removed = sum(_coerce_count(item.get("removed")) for item in files)
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


def changed_file_lines(files_payload: dict[str, Any], limit: int = 6, locale: str = "en") -> list[str]:
    files = [item for item in files_payload.get("files", []) if isinstance(item, dict)] if isinstance(files_payload, dict) else []
    lines = [file_label(item) for item in files[:limit]]
    extra = len(files) - limit
    if extra > 0:
        lines.append(server_string(locale, "common.more", count=extra))
    return lines


def agent_label(agent_name: str, locale: str = "en") -> str:
    return {
        "codex": "Codex",
        "claude": "Claude",
        "term": server_string(locale, "shortcuts.section.terminal"),
        "terminal": server_string(locale, "shortcuts.section.terminal"),
    }.get(agent_name.lower(), agent_name or server_string(locale, "state.noAgent"))


def agent_display_label(agent: AgentInfo | None, fallback_kind: str = "", locale: str = "en") -> str:
    label = agent_label(agent.kind if agent else fallback_kind, locale)
    model = (agent.model or "").strip() if agent else ""
    if model:
        return f"{label} {model}"
    return label


def repo_label(repo: str) -> str:
    return Path(repo).name if repo else ""


def files_sentence(files: dict[str, Any], locale: str = "en") -> str:
    count = int(files.get("count") or 0)
    added = int(files.get("added") or 0)
    removed = int(files.get("removed") or 0)
    if not count:
        return server_string(locale, "summary.files.none")
    return server_plural(locale, "summary.files.count", count, added=added, removed=removed)


def repo_sentence(repos: list[str], locale: str = "en") -> str:
    labels = [repo_label(repo) for repo in repos[:3] if repo]
    if not labels:
        return server_string(locale, "summary.repo.none")
    return localized_list(locale, labels)


def work_sentence(work: str, goal: str, locale: str = "en") -> str:
    work_text = truncate_text(work or goal, 180)
    if not work_text:
        return server_string(locale, "summary.work.none")
    if work and goal and work.strip() != goal.strip():
        return server_string(locale, "summary.work.forGoal", work=work_text, goal=truncate_text(goal, 120))
    return server_string(locale, "summary.work.single", work=work_text)


def unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = truncate_text(str(value or "").strip(), 120)
        if text and text not in result:
            result.append(text)
    return result


def human_status_headline(topics: list[str], repos: list[str], files: dict[str, Any], active_count: int, total_count: int, locale: str = "en") -> str:
    if total_count <= 0:
        return server_string(locale, "det.noActivity")
    work_items = unique_nonempty(topics)
    repo_items = unique_nonempty([repo_label(repo) for repo in repos])
    most_recent = work_items[0] if work_items else server_string(locale, "summary.topic.current")
    target = localized_list(locale, repo_items[:3]) if repo_items else server_string(locale, "summary.workspace.current")
    purpose = server_string(locale, "summary.purpose.finish", work=most_recent) if work_items else server_string(locale, "summary.purpose.progress")
    active_text = server_plural(locale, "summary.activeCount", total_count, active=active_count, total=total_count)
    sentences = [server_string(locale, "summary.headline.main", work=most_recent, target=target, purpose=purpose)]
    other_work = localized_list(locale, work_items[1:4])
    if other_work:
        sentences.append(server_string(locale, "summary.headline.otherWork", work=other_work))
    sentences.append(server_string(locale, "summary.headline.soFar", files=files_sentence(files, locale), active=active_text))
    return " ".join(sentences)


def summary_last_activity_ts(summary: dict[str, Any]) -> float:
    value = summary.get("last_activity_ts")
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    return timestamp if timestamp > 0 else 0.0


def summary_work_label(summary: dict[str, Any], locale: str = "en") -> str:
    fallback = server_string(locale, "summary.work.this")
    return truncate_text(str(summary.get("work") or summary.get("goal") or summary.get("ci") or fallback), 90)


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


def tmux_session_label(session: Any, locale: str = "en") -> str:
    return server_string(locale, "summary.sessionLabel", session=f"`{session}`")


def global_recommendation_sentence(session_summaries: list[dict[str, Any]], locale: str = "en") -> str:
    if not session_summaries:
        return ""
    active = [item for item in session_summaries if item.get("active")]
    target = active[0] if active else freshest_summary(session_summaries)
    if not target:
        return ""
    session = target.get("session")
    work = summary_work_label(target, locale)
    last = str(target.get("last_activity_text") or "").strip()
    recency = server_string(locale, "summary.recommendation.lastWorked", time=last) if last else ""
    if active:
        return server_string(locale, "summary.recommendation.active", session=tmux_session_label(session, locale), work=work, recency=recency)
    return server_string(locale, "summary.recommendation.resume", session=tmux_session_label(session, locale), work=work, recency=recency)


def stale_work_sentence(session_summaries: list[dict[str, Any]], locale: str = "en") -> str:
    stale = stale_summary(session_summaries)
    if not stale:
        return ""
    session = stale.get("session")
    work = summary_work_label(stale, locale)
    last = str(stale.get("last_activity_text") or "").strip()
    return server_string(locale, "summary.recommendation.stale", session=tmux_session_label(session, locale), work=work, time=last)


def project_status_sentence(project: dict[str, Any], files: dict[str, Any], active: bool, locale: str = "en") -> str:
    parts: list[str] = []
    git = project_git(project)
    pr = project.get("pull_request") if isinstance(project, dict) else None
    ci = ci_summary(project, locale)
    if ci:
        parts.append(ci)
    if isinstance(git, dict):
        dirty_count = git.get("dirty_count")
        ahead = git.get("ahead")
        behind = git.get("behind")
        if isinstance(dirty_count, int) and dirty_count > 0:
            parts.append(server_plural(locale, "summary.dirtyFiles", dirty_count))
        if isinstance(ahead, int) and ahead > 0:
            parts.append(server_plural(locale, "summary.commitsAhead", ahead))
        if isinstance(behind, int) and behind > 0:
            parts.append(server_plural(locale, "summary.commitsBehind", behind))
    if isinstance(pr, dict) and pr.get("url"):
        number = pr.get("number")
        parts.append(server_string(locale, "summary.pr.number", number=number) if number else server_string(locale, "summary.pr.linked"))
    if int(files.get("count") or 0) and not parts:
        parts.append(server_string(locale, "summary.status.ready"))
    if not parts:
        parts.append(server_string(locale, "summary.status.active" if active else "summary.status.waiting"))
    return "; ".join(parts)


def repo_names(project: dict[str, Any], files_payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    git = project_git(project)
    if git.get("root"):
        names.append(str(git["root"]))
    for item in files_payload.get("repos", []) if isinstance(files_payload, dict) else []:
        repo = item.get("repo") if isinstance(item, dict) else None
        if isinstance(repo, str) and repo and repo not in names:
            names.append(repo)
    return names


def ci_summary(project: dict[str, Any], locale: str = "en") -> str:
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
            return server_string(locale, "common.ciState", state=state)
    state = pr.get("state")
    if isinstance(state, str) and state:
        return server_string(locale, "summary.pr.state", state=state)
    return ""


def build_session_activity_summary(info: SessionInfo, project: dict[str, Any], files_payload: dict[str, Any], locale: str = "en") -> dict[str, Any]:
    agent = agent_for_summary(info)
    items = recent_transcript_items(agent)
    activity_state = session_transcript_activity_state(info)
    last_activity = transcript_last_activity(agent, locale)
    git = project_git(project)
    pr = project.get("pull_request") if isinstance(project, dict) else None
    goal = latest_item_text(items, "user")
    latest_tool = latest_item_text(items, "tool_use")
    latest_assistant = latest_item_text(items, "assistant")
    work = ""
    if isinstance(pr, dict):
        work = str(pr.get("title") or pr.get("description") or "")
    if not work and git:
        work = str(git.get("subject") or git.get("branch") or "")
    if not work:
        work = latest_assistant
    files = changed_file_totals(files_payload)
    file_lines = changed_file_lines(files_payload, locale=locale)
    repos = repo_names(project, files_payload)
    agent_name = agent.kind if agent else "no agent"
    state_label, active = recent_activity_label(activity_state, last_activity, locale=locale)
    ci = ci_summary(project, locale)
    status = project_status_sentence(project, files, activity_state.get("key") == "working", locale)
    recent_files = server_string(locale, "summary.session.recentFiles", files=", ".join(file_lines[:3])) if file_lines else ""
    local_parts = [
        server_string(locale, "summary.session.identity", agent=agent_display_label(agent, locale=locale), session=info.session, state=state_label, repos=repo_sentence(repos, locale)),
        server_string(locale, "summary.session.lastWorked", time=last_activity["text"]) if last_activity.get("text") else "",
        work_sentence(work, goal, locale),
        server_string(locale, "summary.session.files", files=files_sentence(files, locale), recent=recent_files),
        server_string(locale, "summary.session.status", status=status),
    ]
    local = " ".join(part for part in local_parts if part)
    lines = [local]
    if repos:
        branch = f" @ {git.get('branch')}" if isinstance(git, dict) and git.get("branch") else ""
        lines.append(server_string(locale, "common.repoDetail", repo=f"{repo_label(repos[0])}{branch}"))
    if goal:
        lines.append(server_string(locale, "summary.line.goal", goal=goal))
    if work:
        lines.append(server_string(locale, "common.workDetail", work=truncate_text(work, 180)))
    if latest_tool:
        lines.append(server_string(locale, "summary.line.latestTool", tool=latest_tool))
    if ci:
        lines.append(ci)
    if files["count"]:
        lines.append(server_string(locale, "common.filesDetail", files=files_sentence(files, locale)))
    return {
        "locale": locale,
        "session": info.session,
        "agent": agent_name,
        "agent_model": agent.model if agent else "",
        "agent_label": agent_display_label(agent, agent_name, locale),
        "agent_status": agent.status if agent else "",
        "active": active,
        "activity_label": state_label,
        "activity_key": activity_state.get("key") or "idle",
        "state": activity_state,
        "last_activity": last_activity,
        "last_activity_text": last_activity.get("text") or "",
        "last_activity_ts": last_activity.get("timestamp"),
        "repos": repos,
        "goal": goal,
        "work": truncate_text(work, 220) if work else "",
        "pr_number": pr.get("number") if isinstance(pr, dict) else None,
        "ci": ci,
        "status_text": status,
        "files": files,
        "file_lines": file_lines,
        "lines": lines,
        "local": local,
    }


def build_global_activity_summary(session_summaries: list[dict[str, Any]], errors: list[str] | None = None, locale: str = "en") -> dict[str, Any]:
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
        headline = human_status_headline(topics, repos, {"count": file_count, "added": added, "removed": removed}, len(active), total, locale)
    else:
        headline = server_string(locale, "det.noActivity")
    detail_lines: list[str] = []
    for global_line in (global_recommendation_sentence(session_summaries, locale), stale_work_sentence(session_summaries, locale)):
        if global_line:
            detail_lines.append(global_line)
    session_lines: list[str] = []
    for item in session_summaries:
        status = summary_active_state_label(item, locale)
        repos = item.get("repos") or []
        repo = repo_label(str(repos[0])) if repos else server_string(locale, "summary.repo.none")
        files = item.get("files", {})
        work = item.get("work") or item.get("goal") or item.get("ci") or ""
        agent_text = str(item.get("agent_label") or agent_label(str(item.get("agent") or ""), locale))
        status_text = str(item.get("status_text") or status)
        session_lines.append(server_string(locale, "summary.global.session", session=tmux_session_label(item.get("session"), locale), agent=agent_text, state=status, repo=repo, files=files_sentence(files, locale), status=status_text, work=truncate_text(str(work), 150)))
    for error in errors or []:
        detail_lines.append(server_string(locale, "status.activitySummaryFailed", error=error))
    return {
        "locale": locale,
        "active_agents": len(active),
        "total_agents": len(session_summaries),
        "files": {"count": file_count, "added": added, "removed": removed},
        "headline": headline,
        "detail_lines": detail_lines,
        "session_lines": session_lines,
        "lines": [headline, *detail_lines, *session_lines],
    }


def yoagent_context_lines(activity_payload: dict[str, Any], locale: str = "en") -> list[str]:
    global_summary = activity_payload.get("global") if isinstance(activity_payload, dict) else {}
    sessions = activity_payload.get("sessions") if isinstance(activity_payload, dict) else {}
    capabilities = activity_payload.get("capabilities") if isinstance(activity_payload, dict) else {}
    skills = activity_payload.get("yoagent_skills") if isinstance(activity_payload, dict) else {}
    lines: list[str] = []
    if isinstance(capabilities, dict):
        for line in capabilities.get("lines") or []:
            text = str(line or "").strip()
            if text:
                lines.append(server_string(locale, "summary.context.capability", text=text))
    if isinstance(global_summary, dict):
        headline = str(global_summary.get("headline") or "").strip()
        if headline:
            lines.append(server_string(locale, "summary.context.global", text=headline))
        for line in global_summary.get("lines") or []:
            text = str(line or "").strip()
            if text and text != headline:
                lines.append(server_string(locale, "summary.context.globalDetail", text=text))
    if isinstance(sessions, dict):
        for session, summary in sessions.items():
            if not isinstance(summary, dict):
                continue
            agent = str(summary.get("agent_label") or agent_label(str(summary.get("agent") or ""), locale))
            repos = ", ".join(repo_label(str(repo)) for repo in summary.get("repos") or []) or server_string(locale, "summary.repo.none")
            files = files_sentence(summary.get("files") or {}, locale)
            work = str(summary.get("work") or summary.get("goal") or "").strip()
            status = str(summary.get("status_text") or "").strip()
            state = summary_active_state_label(summary, locale)
            parts = [server_string(locale, "summary.context.session", session=tmux_session_label(session, locale), repos=repos, agent=agent, state=state, files=files)]
            last_activity = str(summary.get("last_activity_text") or "").strip()
            if last_activity:
                parts.append(server_string(locale, "summary.context.lastWorked", time=last_activity))
            if work:
                parts.append(server_string(locale, "common.workDetail", work=truncate_text(work, 180)))
            if status:
                parts.append(server_string(locale, "summary.context.status", status=truncate_text(status, 160)))
            rolling_summary = str(summary.get("rolling_summary") or "").strip()
            if rolling_summary:
                rolling_state = str(summary.get("rolling_state") or "idle").strip()
                parts.append(server_string(locale, "summary.context.transcript", state=rolling_state, text=truncate_text(rolling_summary, 260)))
            file_lines = [str(item) for item in summary.get("file_lines") or [] if item]
            if file_lines:
                parts.append(server_string(locale, "common.filesDetail", files=", ".join(file_lines[:6])))
            lines.append("; ".join(parts))
    if isinstance(skills, dict):
        for line in skills.get("context_lines") or []:
            text = str(line or "").strip()
            if text:
                lines.append(server_string(locale, "summary.context.skill", text=text))
    errors = activity_payload.get("errors") if isinstance(activity_payload, dict) else []
    for error in errors or []:
        lines.append(server_string(locale, "common.errorDetail", error=error))
    return lines


YOAGENT_HISTORY_TURN_LIMIT = 4

YOAGENT_CAPABILITY_KEYS = tuple(f"yoagent.capability.{name}" for name in (
    "readPanes", "poll", "send", "perspective", "handoff", "sequential", "wait", "returnResult", "transport", "skills", "monitor", "notify",
))
YOAGENT_ORCHESTRATION_EXAMPLE_KEYS = tuple(f"yoagent.example.{name}" for name in (
    "workNext", "waitThenCheck", "crossCheck", "transform", "testsThenDocs", "showResult", "notifyIdle", "createSkill",
))


def yoagent_capabilities_payload(locale: str = "en") -> dict[str, Any]:
    return {
        "read_tmux": True,
        "poll_sessions": True,
        "monitor": True,
        "notify": True,
        "send_tmux_input": "server-verified-action",
        "return_send_result": True,
        "manage_user_skills": True,
        "settings_operator": True,
        "yoagent_action_tools": True,
        "transport": "visible-pane-paste-return",
        "examples": [server_string(locale, key) for key in YOAGENT_ORCHESTRATION_EXAMPLE_KEYS],
        "registry": product_capability_registry(),
        "lines": [server_string(locale, key) for key in YOAGENT_CAPABILITY_KEYS],
    }


def yoagent_localized_setting(settings: dict[str, Any], key: str, locale: str) -> str:
    value = str(settings.get(key) or "").strip()
    default = str(DEFAULT_SETTINGS["yoagent"].get(key) or "")
    legacy = str(LEGACY_YOAGENT_DEFAULTS.get(key) or "")
    if not value or value == default or value == legacy:
        catalog_key = YOAGENT_DEFAULT_TEXT_KEYS.get(key)
        if catalog_key:
            return server_string(locale, catalog_key)
    return value


def yoagent_system_prompt(settings: dict[str, Any], locale: str = "en") -> str:
    return yoagent_localized_setting(settings, "system_prompt", locale)


def yoagent_intro(settings: dict[str, Any], locale: str = "en") -> str:
    return yoagent_localized_setting(settings, "intro", locale)


def yoagent_output_format(settings: dict[str, Any], locale: str = "en") -> str:
    return yoagent_localized_setting(settings, "format", locale)


def yoagent_concepts_prompt_block(locale: str = "en") -> list[str]:
    return [
        f"{server_string(locale, 'yoagent.prompt.contextGuard')} {server_string(locale, 'yoagent.prompt.toolOutputGuard')}".strip(),
        server_string(locale, "yoagent.prompt.concepts"),
        yolomux_help_primer(locale),
    ]


def build_yoagent_chat_prompt(question: str, activity_payload: dict[str, Any], settings: dict[str, Any], history: list[dict[str, str]] | None = None, locale: str = "en") -> str:
    history_lines = []
    for item in (history or [])[-YOAGENT_HISTORY_TURN_LIMIT:]:
        role_key = "yoagent.prompt.role.user" if item.get("role") == "user" else "yoagent.prompt.role.assistant"
        role = server_string(locale, role_key)
        content = truncate_text(" ".join(str(item.get("content") or "").split()), 600)
        if content:
            history_lines.append(f"{role}: {content}")
    context = "\n".join(yoagent_context_lines(activity_payload, locale)) or server_string(locale, "yoagent.prompt.noActivityContext")
    return "\n\n".join([
        yoagent_system_prompt(settings, locale),
        yoagent_intro(settings, locale),
        yoagent_output_format(settings, locale),
        server_string(locale, "yoagent.prompt.answerLanguage"),
        *yoagent_concepts_prompt_block(locale),
        server_string(locale, "yoagent.prompt.activityContext"),
        context,
        server_string(locale, "yoagent.prompt.recentChat"),
        "\n".join(history_lines) if history_lines else server_string(locale, "yoagent.prompt.none"),
        f'{server_string(locale, "yoagent.prompt.userQuestion")} {question}',
    ])


def build_yoagent_resume_prompt(question: str, activity_payload: dict[str, Any], settings: dict[str, Any], context_changed: bool, locale: str = "en") -> str:
    if context_changed:
        context = "\n".join(yoagent_context_lines(activity_payload, locale)) or server_string(locale, "yoagent.prompt.noActivityContext")
        context_block = server_string(locale, "yoagent.prompt.activityChanged") + "\n" + context
    else:
        context_block = server_string(locale, "yoagent.prompt.activityUnchanged")
    return "\n\n".join([
        server_string(locale, "yoagent.prompt.continueConversation"),
        yoagent_output_format(settings, locale),
        server_string(locale, "yoagent.prompt.answerLanguage"),
        *yoagent_concepts_prompt_block(locale),
        context_block,
        f'{server_string(locale, "yoagent.prompt.userQuestion")} {question}',
    ])


def deterministic_yoagent_help_reply(question: str, locale: str = "en") -> str:
    text = str(question or "").lower()
    if not text:
        return ""
    wants_capabilities = (
        any(phrase in text for phrase in ["capability", "capabilities", "what can", "can you", "can it", "tools", "send command", "send commands"])
        and any(word in text for word in ["tmux", "pane", "poll", "monitor", "notify", "command", "session", "yo!agent", "yoagent", "agent"])
    )
    if wants_capabilities:
        examples = "\n".join(f"- `{server_string(locale, key)}`" for key in YOAGENT_ORCHESTRATION_EXAMPLE_KEYS)
        return "\n".join([
            server_string(locale, "yoagent.help.capabilities.overview"),
            "",
            server_string(locale, "yoagent.help.capabilities.send"),
            "",
            server_string(locale, "yoagent.help.capabilities.handoff"),
            "",
            server_string(locale, "yoagent.help.capabilities.examples"),
            examples,
            "",
            server_string(locale, "yoagent.help.capabilities.transport"),
            "",
            server_string(locale, "yoagent.help.capabilities.skills"),
            "",
            server_string(locale, "yoagent.help.capabilities.guardrails"),
        ])
    wants_skills = (
        "skill" in text
        and any(word in text for word in ["yo!agent", "yoagent", "agent", "built-in", "builtin", "custom", "user"])
    )
    if wants_skills:
        return server_string(locale, "yoagent.help.skills")
    wants_context = any(phrase in text for phrase in ["where do your insights", "where does your insight", "where do you get", "context come", "context comes", "no summary", "no insight", "no transcript", "session 7"])
    if wants_context:
        return server_string(locale, "yoagent.help.contextSource")
    mentions_pane = "pane" in text
    mentions_window = "window" in text
    mentions_tab = "tab" in text
    mentions_finder = "finder" in text or "file explorer" in text
    mentions_split = "split" in text or "drag" in text
    if mentions_pane and ("what" in text or "difference" in text or "mean" in text):
        return server_string(locale, "yoagent.help.pane")
    if mentions_window and ("tmux" in text or mentions_tab or "difference" in text):
        return server_string(locale, "yoagent.help.window")
    if mentions_tab and ("what" in text or "difference" in text or "mean" in text):
        return server_string(locale, "yoagent.help.tab")
    if mentions_finder:
        return server_string(locale, "yoagent.help.finder")
    if mentions_split:
        return server_string(locale, "yoagent.help.split")
    return ""


def yoagent_topic_title(summary: dict[str, Any], locale: str = "en") -> str:
    fallback = server_string(locale, "summary.work.ongoing")
    topic = truncate_text(str(summary.get("work") or summary.get("goal") or summary.get("ci") or fallback), 90)
    suffix_parts: list[str] = []
    repos = summary.get("repos") or []
    if repos:
        suffix_parts.append(repo_label(str(repos[0])))
    pr_number = summary.get("pr_number")
    if pr_number:
        suffix_parts.append(f"PR #{pr_number}")
    suffix = f" — {' · '.join(part for part in suffix_parts if part)}" if any(suffix_parts) else ""
    return f"{topic}{suffix}"


def yoagent_session_topic_title(summary: dict[str, Any], locale: str = "en") -> str:
    session = summary.get("session")
    agent = str(summary.get("agent_label") or agent_label(str(summary.get("agent") or ""), locale)).strip()
    topic = yoagent_topic_title(summary, locale)
    agent_name = str(summary.get("agent") or "").strip().lower()
    if agent_name and agent_name not in {"no agent", "agent"}:
        return server_string(locale, "summary.topic.sessionWithAgent", session=tmux_session_label(session, locale), agent=agent, topic=topic)
    return server_string(locale, "summary.topic.session", session=tmux_session_label(session, locale), topic=topic)


def markdown_table_cell(value: Any) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def markdown_code(value: Any) -> str:
    text = str(value or "").strip().replace("`", "\\`")
    return f"`{text}`" if text else ""


def markdown_session_link(session: Any) -> str:
    text = str(session or "").strip()
    if not text:
        return ""
    return f"[{markdown_code(text)}](?yoagent-session={quote(text, safe='')})"


def yoagent_session_paths(summary: dict[str, Any], locale: str = "en") -> str:
    repos = [str(repo) for repo in summary.get("repos") or [] if repo]
    if not repos:
        return server_string(locale, "common.notAvailable")
    return ", ".join(markdown_code(repo) for repo in repos)


def yoagent_session_details(summary: dict[str, Any], locale: str = "en") -> str:
    agent = str(summary.get("agent_label") or agent_label(str(summary.get("agent") or ""), locale))
    state = summary_active_state_label(summary, locale)
    files = summary.get("files") or {}
    details = server_string(locale, "summary.table.details", agent=agent, state=state, files=files_sentence(files, locale))
    goal = str(summary.get("goal") or "").strip()
    extras: list[str] = []
    if goal and goal != str(summary.get("work") or ""):
        extras.append(server_string(locale, "summary.line.goal", goal=truncate_text(goal, 120)))
    ci = str(summary.get("ci") or "").strip()
    if ci:
        extras.append(server_string(locale, "summary.line.ci", ci=ci))
    status = str(summary.get("status_text") or "").strip()
    if status and status != ci:
        extras.append(server_string(locale, "summary.context.status", status=truncate_text(status, 120)))
    file_lines = [str(item) for item in summary.get("file_lines") or [] if item]
    if file_lines:
        extras.append(server_string(locale, "common.filesDetail", files=", ".join(file_lines[:3])))
    if extras:
        return server_string(locale, "summary.table.detailsWithExtras", details=details, extras="; ".join(extras))
    return server_string(locale, "summary.table.detailsOnly", details=details)


def compact_last_worked_text(text: Any, locale: str = "en", timestamp: Any = None) -> str:
    try:
        timestamp_value = float(timestamp or 0)
    except (TypeError, ValueError):
        timestamp_value = 0.0
    if timestamp_value > 0:
        return compact_relative_age_text(timestamp_value, locale=locale)
    value = str(text or "").strip()
    if not value:
        return server_string(locale, "common.notAvailable")
    return value


def yoagent_session_table(summaries: list[dict[str, Any]], locale: str = "en") -> list[str]:
    """One Markdown table for session summary/list answers."""
    lines = [
        f"| {server_string(locale, 'summary.table.session')} | {server_string(locale, 'summary.table.path')} | {server_string(locale, 'summary.table.lastWorked')} | {server_string(locale, 'common.details')} |",
        "|---|---|---|---|",
    ]
    for summary in summaries:
        session = markdown_table_cell(markdown_session_link(summary.get("session")))
        paths = yoagent_session_paths(summary, locale)
        last = markdown_table_cell(compact_last_worked_text(summary.get("last_activity_text"), locale, summary.get("last_activity_ts")))
        details = markdown_table_cell(yoagent_session_details(summary, locale))
        lines.append(f"| {session} | {paths} | {last} | {details} |")
    return lines


def yoagent_question_requests_session_list(question: str) -> bool:
    text = str(question or "").lower()
    list_phrases = [
        "list sessions",
        "list all sessions",
        "list every session",
        "enumerate sessions",
        "enumerate all sessions",
        "summary",
        "summarize",
        "summarise",
        "summarize sessions",
        "summarise sessions",
        "session summary",
        "summaries",
        "what did we work on",
        "what we worked on",
        "worked on today",
        "show sessions",
        "show all sessions",
        "one by one",
        "all sessions",
        "every session",
        "each session",
        "per session",
        "session by session",
    ]
    return any(phrase in text for phrase in list_phrases)


def yoagent_question_named_sessions(question: str) -> set[str]:
    text = str(question or "").lower()
    return set(re.findall(r"\bsession\s+([A-Za-z0-9_.-]{1,64})\b", text))


def yoagent_question_requests_work_next(question: str) -> bool:
    text = str(question or "").lower()
    return any(
        phrase in text
        for phrase in (
            "what should i work on",
            "what should i do next",
            "what is next",
            "what's next",
            "prioritize my sessions",
            "prioritise my sessions",
        )
    )


def yoagent_question_requests_full_work_inventory(question: str) -> bool:
    text = str(question or "").lower()
    return yoagent_question_requests_work_next(question) and any(
        phrase in text
        for phrase in (
            "full inventory",
            "full list",
            "show all",
            "list all",
            "all sessions",
            "every session",
            "everything",
        )
    )


def yoagent_summary_project(summary: dict[str, Any]) -> dict[str, Any]:
    project = summary.get("project")
    if isinstance(project, dict):
        return project
    return {}


def yoagent_summary_git(summary: dict[str, Any]) -> dict[str, Any]:
    git = summary.get("git")
    if isinstance(git, dict):
        return git
    project = yoagent_summary_project(summary)
    return project_git(project)


def yoagent_summary_pull_request(summary: dict[str, Any]) -> dict[str, Any]:
    pr = summary.get("pull_request")
    if isinstance(pr, dict):
        return pr
    project = yoagent_summary_project(summary)
    pr = project.get("pull_request")
    return pr if isinstance(pr, dict) else {}


def yoagent_summary_ci(summary: dict[str, Any]) -> dict[str, Any]:
    ci = summary.get("ci")
    if isinstance(ci, dict):
        return ci
    pr = yoagent_summary_pull_request(summary)
    checks = pr.get("checks")
    return checks if isinstance(checks, dict) else {}


def yoagent_summary_priority_hints(summary: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("work_priority_reasons", "priority_reasons", "user_priority_reasons"):
        values = summary.get(key)
        if isinstance(values, list):
            for item in values:
                text = truncate_text(str(item or "").strip(), 120)
                if text and text not in hints:
                    hints.append(text)
    for key in ("work_priority", "user_priority", "priority"):
        text = truncate_text(str(summary.get(key) or "").strip(), 120)
        if text and text not in hints:
            hints.append(text)
    return hints


def yoagent_text_contains_failure(text: str) -> bool:
    value = text.lower()
    return any(term in value for term in ("failing", "failed", "failure", "red", "broken"))


def yoagent_work_recommendation(summary: dict[str, Any], locale: str = "en") -> dict[str, Any]:
    state = summary.get("state") if isinstance(summary.get("state"), dict) else {}
    state_key = str(state.get("key") or summary.get("activity_key") or "").strip().lower()
    state_text = str(state.get("text") or "").strip()
    status = str(summary.get("status_text") or "").strip()
    ci_text = str(summary.get("ci") or "").strip()
    blockers = [str(item) for item in summary.get("blockers") or [] if item]
    errors = [str(item) for item in summary.get("errors") or [] if item]
    combined_text = " ".join(str(item or "") for item in (
        state_key, state_text, status, ci_text, summary.get("local"), summary.get("work"), summary.get("goal"), " ".join(blockers), " ".join(errors),
    ))
    files = summary.get("files") if isinstance(summary.get("files"), dict) else {}
    git = yoagent_summary_git(summary)
    pr = yoagent_summary_pull_request(summary)
    ci = yoagent_summary_ci(summary)
    dirty_count = git.get("dirty_count")
    files_count = files.get("count")
    ci_state = str(ci.get("state") or "").strip().lower()
    review_decision = str(pr.get("review_decision") or "").strip().upper()
    priority_hints = yoagent_summary_priority_hints(summary)
    reasons: list[str] = []
    next_action = server_string(locale, "summary.work.next.open")
    score = 100
    if state_key in {"needs-input", "needs_input", "needs input"}:
        score = 1000
        reasons.append(server_string(locale, "summary.work.reason.needsInputWithText", text=truncate_text(state_text, 100)) if state_text else server_string(locale, "summary.work.reason.needsInput"))
        next_action = server_string(locale, "summary.work.next.answer")
    elif state_key in {"blocked", "needs-approval", "approval"} or blockers or errors or "blocked" in combined_text.lower():
        score = 900
        blocker_text = state_text or (blockers[0] if blockers else errors[0] if errors else "")
        reasons.append(server_string(locale, "summary.work.reason.blockedWithText", text=truncate_text(blocker_text, 100)) if blocker_text else server_string(locale, "summary.work.reason.blocked"))
        next_action = server_string(locale, "summary.work.next.unblock")
    elif "test" in combined_text.lower() and yoagent_text_contains_failure(combined_text):
        score = 850
        reasons.append(server_string(locale, "summary.work.reason.testsFailing"))
        next_action = server_string(locale, "summary.work.next.tests")
    elif ci_state == "failing" or ("ci" in combined_text.lower() and yoagent_text_contains_failure(combined_text)):
        score = 820
        reasons.append(str(ci.get("summary") or ci_text or server_string(locale, "summary.work.reason.ciFailing")))
        next_action = server_string(locale, "summary.work.next.ci")
    elif review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"} or any(phrase in combined_text.lower() for phrase in ("review required", "changes requested", "review comment")):
        score = 780
        reasons.append(server_string(locale, "summary.work.reason.review"))
        next_action = server_string(locale, "summary.work.next.review")
    elif isinstance(dirty_count, int) and dirty_count > 0:
        score = 650
        reasons.append(server_plural(locale, "summary.dirtyFiles", dirty_count))
        next_action = server_string(locale, "summary.work.next.dirty")
    elif isinstance(files_count, int) and files_count > 0:
        score = 620
        reasons.append(files_sentence(files, locale))
        next_action = server_string(locale, "summary.work.next.files")
    elif summary.get("active"):
        score = 500
        reasons.append(server_string(locale, "summary.state.recent"))
        next_action = server_string(locale, "summary.work.next.continue")
    else:
        reasons.append(server_string(locale, "summary.state.idle"))
        next_action = server_string(locale, "summary.work.next.resumeLater")
    if priority_hints:
        reason = server_string(locale, "summary.work.reason.localPriority", text=priority_hints[0])
        if reason not in reasons:
            reasons.append(reason)
        if score < 740:
            score = 740
            next_action = server_string(locale, "summary.work.next.localPriority")
        else:
            score += 25
    if not reasons and ci_text:
        reasons.append(ci_text)
    return {
        "score": score,
        "reasons": reasons,
        "next_action": next_action,
        "last_activity_ts": summary_last_activity_ts(summary),
        "session": str(summary.get("session") or ""),
    }


def yoagent_rank_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(summaries, key=lambda summary: (0 if summary.get("active") else 1, -summary_last_activity_ts(summary)))


def yoagent_rank_work_summaries(summaries: list[dict[str, Any]], locale: str = "en") -> list[dict[str, Any]]:
    return sorted(
        summaries,
        key=lambda summary: (
            -int(yoagent_work_recommendation(summary, locale)["score"]),
            -summary_last_activity_ts(summary),
            str(summary.get("session") or ""),
        ),
    )


def yoagent_default_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = yoagent_rank_summaries(summaries)
    active = [summary for summary in ranked if summary.get("active")]
    if active:
        return active[:3]
    return ranked[:1]


def yoagent_default_work_summaries(summaries: list[dict[str, Any]], locale: str = "en") -> list[dict[str, Any]]:
    return yoagent_rank_work_summaries(summaries, locale)[:3]


def yoagent_work_priority_hints(activity_payload: dict[str, Any]) -> dict[str, list[str]]:
    if not isinstance(activity_payload, dict):
        return {}
    by_session: dict[str, list[str]] = {}

    def add(session: Any, reason: Any) -> None:
        key = str(session or "").strip()
        text = truncate_text(str(reason or "").strip(), 120)
        if not key or not text:
            return
        by_session.setdefault(key, [])
        if text not in by_session[key]:
            by_session[key].append(text)

    def add_from_value(value: Any) -> None:
        if isinstance(value, dict):
            if "session" in value:
                add(value.get("session"), value.get("reason") or value.get("text") or value.get("label") or value.get("priority"))
                return
            for session, reason in value.items():
                add(session, reason)
        elif isinstance(value, list):
            for item in value:
                add_from_value(item)

    for key in ("work_priorities", "user_priorities", "priorities"):
        add_from_value(activity_payload.get(key))

    skills = activity_payload.get("yoagent_skills")
    context_lines = skills.get("context_lines") if isinstance(skills, dict) else []
    for raw_line in context_lines or []:
        line = str(raw_line or "").strip()
        if not re.search(r"\b(?:work[- ]?next\s+priority|work\s+priority|priority\s+session|prioriti[sz]e\s+session)\b", line, re.IGNORECASE):
            continue
        match = re.search(r"\bsession\s+([A-Za-z0-9_.-]{1,64})\b\s*(?::|-|\u2014)?\s*(.*)$", line, re.IGNORECASE)
        if match:
            add(match.group(1), match.group(2) or line)
    return by_session


def yoagent_work_advice_line(summary: dict[str, Any], locale: str = "en") -> str:
    title = yoagent_topic_title(summary, locale)
    state = summary_active_state_label(summary, locale)
    last = str(summary.get("last_activity_text") or "").strip()
    files = files_sentence(summary.get("files") or {}, locale)
    details = [state]
    if last:
        details.append(server_string(locale, "summary.work.lastWorked", time=last))
    details.append(files)
    ci = str(summary.get("ci") or summary.get("status_text") or "").strip()
    if ci:
        details.append(truncate_text(ci, 120))
    return server_string(locale, "summary.work.advice", title=title, details="; ".join(part for part in details if part))


def yoagent_ranked_work_advice_line(summary: dict[str, Any], locale: str = "en") -> str:
    recommendation = yoagent_work_recommendation(summary, locale)
    title = yoagent_topic_title(summary, locale)
    reasons = "; ".join(str(item) for item in recommendation["reasons"] if item)
    return server_string(locale, "summary.work.rankedAdvice", title=title, reasons=reasons, next=recommendation["next_action"])


def yoagent_default_pending_lines(summaries: list[dict[str, Any]], locale: str = "en") -> list[str]:
    if not summaries:
        return []
    ranked = yoagent_rank_work_summaries(summaries, locale)
    target = ranked[0] if ranked else None
    pending: list[str] = []
    if target:
        work = summary_work_label(target, locale)
        last = str(target.get("last_activity_text") or "").strip()
        recency = server_string(locale, "summary.pending.recency", time=last) if last else ""
        if target.get("active"):
            pending.append(server_string(locale, "summary.pending.active", work=work, recency=recency))
        else:
            pending.append(server_string(locale, "summary.pending.resume", work=work, recency=recency))
    stale = stale_summary(summaries)
    if stale:
        work = summary_work_label(stale, locale)
        last = str(stale.get("last_activity_text") or "").strip()
        suffix = server_string(locale, "summary.pending.staleTime", time=last) if last else ""
        pending.append(server_string(locale, "summary.pending.stale", work=work, time=suffix))
    return pending


def deterministic_yoagent_reply(question: str, activity_payload: dict[str, Any], settings: dict[str, Any] | None = None, locale: str = "en") -> str:
    help_reply = deterministic_yoagent_help_reply(question, locale)
    if help_reply:
        return help_reply
    no_activity = server_string(locale, "det.noActivity")
    global_summary = activity_payload.get("global") if isinstance(activity_payload, dict) else {}
    sessions = activity_payload.get("sessions") if isinstance(activity_payload, dict) else {}
    headline = str(global_summary.get("headline") or no_activity) if isinstance(global_summary, dict) else no_activity
    question_text = str(question or "").lower()
    all_summaries: list[dict[str, Any]] = []
    work_priority_hints = yoagent_work_priority_hints(activity_payload)
    if isinstance(sessions, dict):
        for key, summary in sessions.items():
            if not isinstance(summary, dict):
                continue
            # Real payloads carry "session"; fall back to the dict key so the section title is labeled.
            item = summary if summary.get("session") else {**summary, "session": key}
            session_key = str(item.get("session") or key)
            session_info = activity_payload.get("session_info") if isinstance(activity_payload, dict) else {}
            details = session_info.get(session_key) if isinstance(session_info, dict) else None
            if isinstance(details, dict) and "project" not in item:
                item = {**item, "project": details.get("project") if isinstance(details.get("project"), dict) else {}}
            if work_priority_hints.get(session_key):
                item = {**item, "work_priority_reasons": [*yoagent_summary_priority_hints(item), *work_priority_hints[session_key]]}
            all_summaries.append(item)
    # Session detail is opt-in: only expose session ids when the user explicitly asks for sessions or
    # names a concrete "session N". Default answers stay task/advice-shaped.
    named_sessions = yoagent_question_named_sessions(question)
    selected = [summary for summary in all_summaries if summary.get("session") and str(summary.get("session")).lower() in named_sessions]
    if selected:
        chosen = yoagent_rank_summaries(selected)
    elif yoagent_question_requests_full_work_inventory(question):
        chosen = yoagent_rank_work_summaries(all_summaries, locale)
    elif yoagent_question_requests_session_list(question):
        chosen = yoagent_rank_summaries(all_summaries)
    elif yoagent_question_requests_work_next(question):
        chosen = yoagent_default_work_summaries(all_summaries, locale)
    else:
        chosen = yoagent_default_summaries(all_summaries)
    prefix = server_string(locale, "det.noBackend")
    out = [prefix, "", headline]
    if not chosen:
        return "\n".join(out).strip()
    out.append("")
    include_session_details = bool(selected) or yoagent_question_requests_session_list(question)
    if include_session_details:
        out.extend(yoagent_session_table(chosen, locale))
        out.append("")
        pending_scope = chosen if selected else all_summaries
        pending: list[str] = []
        recommendation = global_recommendation_sentence(pending_scope, locale)
        if recommendation:
            pending.append(f"- {recommendation}")
        stale = stale_work_sentence(pending_scope, locale)
        if stale:
            pending.append(f"- {stale}")
    else:
        out.append(f'**{server_string(locale, "det.priority")}**')
        for summary in chosen:
            if yoagent_question_requests_work_next(question):
                out.append(yoagent_ranked_work_advice_line(summary, locale))
            else:
                out.append(yoagent_work_advice_line(summary, locale))
        pending = yoagent_default_pending_lines(all_summaries, locale)
    if pending:
        out.append(f'**{server_string(locale, "det.openPending")}**')
        out.extend(pending)
    return "\n".join(out).strip()

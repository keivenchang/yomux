# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Repo-aware AI file-change attribution for live sessions."""

from __future__ import annotations

from .common import *
from .filesystem import git_root_for_path


CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CODEX_PATCH_RE = re.compile(r"\*\*\* (Add|Update|Delete) File: ([^\"\\\n]+)")
CODEX_PATCH_STATUS = {"Add": "A", "Update": "M", "Delete": "D"}
SESSION_FILES_MAX_HOURS = 24 * 14


def classify_change(markers: set[str]) -> str:
    if "A" in markers:
        return "A"
    if "D" in markers:
        return "D"
    return "M"


def resolved_change_path(raw_path: str, cwd: str | None) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        if not cwd:
            return None
        path = Path(cwd).expanduser() / path
    return path.resolve(strict=False)


def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def scan_claude_transcript(path: Path, cwd: str | None = None) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                for item in message.get("content", []) or []:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    tool = item.get("name")
                    if tool not in CLAUDE_EDIT_TOOLS:
                        continue
                    payload = item.get("input")
                    file_path = payload.get("file_path") if isinstance(payload, dict) else None
                    resolved = resolved_change_path(file_path or "", cwd)
                    if resolved is None:
                        continue
                    changes.setdefault(str(resolved), set()).add("A" if tool == "Write" else "M")
    except OSError:
        return changes
    return changes


def scan_codex_transcript(path: Path, cwd: str | None = None) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                for verb, raw_path in CODEX_PATCH_RE.findall(line):
                    resolved = resolved_change_path(raw_path, cwd)
                    if resolved is None:
                        continue
                    changes.setdefault(str(resolved), set()).add(CODEX_PATCH_STATUS[verb])
    except OSError:
        return changes
    return changes


def scan_agent_changes(agent: AgentInfo) -> dict[str, set[str]]:
    if not agent.transcript:
        return {}
    path = Path(agent.transcript).expanduser()
    if agent.kind == "claude":
        return scan_claude_transcript(path, agent.cwd)
    if agent.kind == "codex":
        return scan_codex_transcript(path, agent.cwd)
    return {}


def bounded_session_files_hours(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 24.0
    return max(0.25, min(parsed, float(SESSION_FILES_MAX_HOURS)))


def git_name_status(repo: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    diff = run_cmd(["git", "-C", str(repo), "diff", "--name-status", "HEAD"], timeout=5.0)
    if diff.returncode == 0:
        for line in diff.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0][0]
            rel_path = parts[-1]
            statuses[rel_path] = "D" if status == "D" else "M"
    untracked = run_cmd(["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard"], timeout=5.0)
    if untracked.returncode == 0:
        for rel_path in untracked.stdout.splitlines():
            if rel_path:
                statuses[rel_path] = "A"
    return statuses


def parse_numstat_value(value: str) -> int | None:
    if value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def git_numstat(repo: Path) -> dict[str, dict[str, int | None]]:
    counts: dict[str, dict[str, int | None]] = {}
    diff = run_cmd(["git", "-C", str(repo), "diff", "--numstat", "HEAD"], timeout=5.0)
    if diff.returncode != 0:
        return counts
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rel_path = parts[-1]
        counts[rel_path] = {
            "added": parse_numstat_value(parts[0]),
            "removed": parse_numstat_value(parts[1]),
        }
    return counts


def untracked_added_line_count(path: Path) -> int | None:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)


def repo_relative_path(path: Path, repo: Path) -> str | None:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return None


def session_file_entry(
    session: str,
    agent: str,
    status: str,
    path: Path,
    repo: Path | None,
    source: str,
    added: int | None = None,
    removed: int | None = None,
) -> dict[str, Any]:
    rel_path = repo_relative_path(path, repo) if repo else None
    return {
        "session": session,
        "agent": agent,
        "status": status,
        "repo": str(repo) if repo else "",
        "path": rel_path or str(path),
        "abs_path": str(path),
        "mtime": file_mtime(path),
        "source": source,
        "added": added,
        "removed": removed,
    }


def session_files_payload_for_info(info: SessionInfo, hours: float = 24.0, now: float | None = None) -> dict[str, Any]:
    cutoff = (now if now is not None else time.time()) - bounded_session_files_hours(hours) * 3600
    touched: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for agent in info.agents:
        if not agent.transcript:
            if agent.error:
                errors.append(agent.error)
            continue
        transcript = Path(agent.transcript).expanduser()
        if file_mtime(transcript) < cutoff:
            continue
        for path_text, markers in scan_agent_changes(agent).items():
            touched[path_text] = {
                "agent": agent.kind,
                "status": classify_change(markers),
            }

    repos: dict[str, set[str]] = {}
    outside_repo: list[dict[str, Any]] = []
    for path_text, metadata in touched.items():
        path = Path(path_text)
        repo_text = git_root_for_path(path)
        if repo_text:
            repos.setdefault(repo_text, set()).add(path_text)
        else:
            outside_repo.append(session_file_entry(info.session, metadata["agent"], metadata["status"], path, None, "tool"))

    files: list[dict[str, Any]] = []
    repo_payloads: list[dict[str, Any]] = []
    for repo_text in sorted(repos):
        repo = Path(repo_text)
        statuses = git_name_status(repo)
        numstat = git_numstat(repo)
        repo_entries: list[dict[str, Any]] = []
        for rel_path, status in statuses.items():
            path = (repo / rel_path).resolve(strict=False)
            counts = numstat.get(rel_path, {})
            added = counts.get("added")
            removed = counts.get("removed")
            if status == "A" and rel_path not in numstat:
                added = untracked_added_line_count(path)
                removed = 0
            agent = next(
                (metadata["agent"] for touched_path, metadata in touched.items() if repo_relative_path(Path(touched_path), repo) == rel_path),
                "",
            )
            repo_entries.append(session_file_entry(info.session, agent, status, path, repo, "git", added, removed))
        repo_entries.sort(key=lambda item: (-float(item.get("mtime") or 0), item["path"]))
        files.extend(repo_entries)
        repo_payloads.append({
            "repo": str(repo),
            "count": len(repo_entries),
            "touched_count": len(repos[repo_text]),
        })

    files.extend(outside_repo)
    files.sort(key=lambda item: (-float(item.get("mtime") or 0), item["repo"], item["path"]))
    return {
        "session": info.session,
        "hours": bounded_session_files_hours(hours),
        "files": files,
        "repos": repo_payloads,
        "errors": errors,
    }


def session_files_payload(session: str | None, infos: dict[str, SessionInfo], hours: float = 24.0) -> tuple[dict[str, Any], HTTPStatus]:
    if session:
        info = infos.get(session)
        if info is None:
            return {"error": f"unknown session: {session}", "session": session}, HTTPStatus.NOT_FOUND
        payload = session_files_payload_for_info(info, hours)
        return payload, HTTPStatus.OK

    files: list[dict[str, Any]] = []
    repos: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for info in infos.values():
        payload = session_files_payload_for_info(info, hours)
        files.extend(payload["files"])
        errors.extend(payload["errors"])
        for repo in payload["repos"]:
            key = repo["repo"]
            existing = repos.setdefault(key, {"repo": key, "count": 0, "touched_count": 0})
            existing["count"] += repo["count"]
            existing["touched_count"] += repo["touched_count"]
    files.sort(key=lambda item: (-float(item.get("mtime") or 0), item["session"], item["path"]))
    return {
        "session": "",
        "hours": bounded_session_files_hours(hours),
        "files": files,
        "repos": sorted(repos.values(), key=lambda item: item["repo"]),
        "errors": errors,
    }, HTTPStatus.OK

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Repo-aware AI file-change attribution for live sessions."""

from __future__ import annotations

import json
import re
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .common import AgentInfo
from .common import SessionInfo
from .common import is_generated_upload_name
from .filesystem import git_root_for_path
from .tmux_utils import run_cmd


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


def file_size(path: Path) -> int | None:
    # C5: Modified-files rows need the same size Finder gets from /api/fs/list so the image hover preview
    # can enforce the same "only preview images under the cap" rule. None when the file is gone (deleted).
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


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


def git_default_branch_ref(repo: Path) -> str | None:
    result = run_cmd(["git", "-C", str(repo), "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], timeout=5.0)
    if result.returncode == 0:
        ref = result.stdout.strip()
        if ref:
            return ref
    for ref in ("origin/main", "origin/master", "main", "master"):
        verify_ref = f"refs/remotes/{ref}" if ref.startswith("origin/") else f"refs/heads/{ref}"
        verify = run_cmd(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", verify_ref], timeout=5.0)
        if verify.returncode == 0:
            return ref
    return None


def git_diff_base(repo: Path) -> str:
    default_ref = git_default_branch_ref(repo)
    if default_ref:
        result = run_cmd(["git", "-C", str(repo), "merge-base", default_ref, "HEAD"], timeout=5.0)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return "HEAD"


def normal_ref(value: str | None, default: str) -> str:
    ref = str(value or "").strip()
    return ref or default


def refs_requested(from_ref: str | None = None, to_ref: str | None = None) -> bool:
    return bool(str(from_ref or "").strip() or str(to_ref or "").strip())


def diff_refs(from_ref: str | None = None, to_ref: str | None = None) -> tuple[str, str]:
    return normal_ref(from_ref, "HEAD"), normal_ref(to_ref, "current")


def git_ref_exists(repo: Path, ref: str) -> bool:
    result = run_cmd(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], timeout=3.0)
    return result.returncode == 0


def validate_diff_refs(repo: Path, from_ref: str, to_ref: str) -> str:
    if to_ref == "current":
        if from_ref == "current":
            return "FROM ref must be older than TO ref (current is the working tree)"
        return "" if git_ref_exists(repo, from_ref) else f"unknown FROM ref: {from_ref}"
    if from_ref == "current":
        return "FROM ref must be older than TO ref (current is the working tree)"
    if not git_ref_exists(repo, from_ref):
        return f"unknown FROM ref: {from_ref}"
    if not git_ref_exists(repo, to_ref):
        return f"unknown TO ref: {to_ref}"
    order = run_cmd(["git", "-C", str(repo), "merge-base", "--is-ancestor", from_ref, to_ref], timeout=5.0)
    if order.returncode != 0:
        return f"FROM ref must be older than TO ref ({from_ref} is not an ancestor of {to_ref})"
    return ""


def git_diff_args(repo: Path, base: str | None = None, from_ref: str | None = None, to_ref: str | None = None) -> tuple[list[str], bool, str]:
    if refs_requested(from_ref, to_ref):
        older, newer = diff_refs(from_ref, to_ref)
        error = validate_diff_refs(repo, older, newer)
        if error:
            return [], False, error
        if newer == "current":
            return [older], True, ""
        return [older, newer], False, ""
    return [base or git_diff_base(repo)], True, ""


def git_recent_refs(repo: Path, limit: int = 100) -> list[dict[str, str]]:
    result = run_cmd(["git", "-C", str(repo), "log", f"--max-count={max(1, min(limit, 200))}", "--pretty=format:%H%x1f%h%x1f%s%x1f%at%x1f%an"], timeout=5.0)
    refs = [{"ref": "HEAD", "short": "HEAD", "subject": "base commit"}, {"ref": "current", "short": "current", "subject": "working tree"}]
    if result.returncode != 0:
        return refs
    seen = {"current", "HEAD"}
    for line in result.stdout.splitlines():
        parts = line.split("\x1f", 4)
        if len(parts) < 3 or not parts[0] or parts[0] in seen:
            continue
        entry: dict[str, str] = {"ref": parts[0], "short": parts[1], "subject": parts[2]}
        if len(parts) >= 5:
            entry["date"] = parts[3]
            entry["author"] = parts[4]
        refs.append(entry)
        seen.add(parts[0])
    return refs


def diff_ref_resolution_error(message: str) -> bool:
    return (
        message.startswith("unknown FROM ref:")
        or message.startswith("unknown TO ref:")
        or message.startswith("FROM ref must be older than TO ref")
    )


def git_name_status(repo: Path, base: str | None = None, from_ref: str | None = None, to_ref: str | None = None) -> tuple[dict[str, str], str]:
    statuses: dict[str, str] = {}
    diff_args, include_untracked, error = git_diff_args(repo, base, from_ref, to_ref)
    if error:
        return statuses, error
    diff = run_cmd(["git", "-C", str(repo), "diff", "--name-status", "-z", "--find-renames", "--find-copies", *diff_args], timeout=5.0)
    if diff.returncode == 0:
        parts = diff.stdout.split("\0")
        index = 0
        while index < len(parts):
            status_text = parts[index]
            index += 1
            if not status_text:
                continue
            status = status_text[0]
            if status in {"R", "C"}:
                new_path = parts[index + 1] if index + 1 < len(parts) else ""
                index += 2
                if new_path:
                    statuses[new_path] = status
                continue
            rel_path = parts[index] if index < len(parts) else ""
            index += 1
            if rel_path:
                statuses[rel_path] = "A" if status == "A" else "D" if status == "D" else "M"
    if include_untracked:
        untracked = run_cmd(["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"], timeout=5.0)
    else:
        untracked = None
    if untracked and untracked.returncode == 0:
        for rel_path in untracked.stdout.split("\0"):
            if rel_path:
                # Untracked working-tree files get "?" (git's own untracked marker — `git status` shows
                # "??"), distinct from a genuine staged/committed add "A" (from `git diff --name-status`
                # above). Both are "new", but "A" means git is tracking the add; "?" means the file is
                # not in the index at all.
                statuses[rel_path] = "?"
    return statuses, ""


def parse_numstat_value(value: str) -> int | None:
    if value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def git_numstat(repo: Path, base: str | None = None, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, dict[str, int | None]]:
    counts: dict[str, dict[str, int | None]] = {}
    diff_args, _, error = git_diff_args(repo, base, from_ref, to_ref)
    if error:
        return counts
    diff = run_cmd(["git", "-C", str(repo), "diff", "--numstat", "-z", "--find-renames", "--find-copies", *diff_args], timeout=5.0)
    if diff.returncode != 0:
        return counts
    parts = diff.stdout.split("\0")
    index = 0
    while index < len(parts):
        head = parts[index]
        index += 1
        if not head:
            continue
        parts_head = head.split("\t", 2)
        if len(parts_head) < 3:
            continue
        if parts_head[2]:
            rel_path = parts_head[2]
        else:
            old_path = parts[index] if index < len(parts) else ""
            new_path = parts[index + 1] if index + 1 < len(parts) else ""
            index += 2
            rel_path = new_path or old_path
        if not rel_path:
            continue
        counts[rel_path] = {
            "added": parse_numstat_value(parts_head[0]),
            "removed": parse_numstat_value(parts_head[1]),
        }
    return counts


def git_ahead_behind(repo: Path, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, int]:
    if refs_requested(from_ref, to_ref):
        older, newer = diff_refs(from_ref, to_ref)
        left_ref = older
        right_ref = "HEAD" if newer == "current" else newer
    else:
        upstream = run_cmd(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=3.0)
        if upstream.returncode != 0 or not upstream.stdout.strip():
            return {}
        left_ref = upstream.stdout.strip()
        right_ref = "HEAD"
    if not git_ref_exists(repo, left_ref) or not git_ref_exists(repo, right_ref):
        return {}
    result = run_cmd(["git", "-C", str(repo), "rev-list", "--left-right", "--count", f"{left_ref}...{right_ref}"], timeout=5.0)
    if result.returncode != 0:
        return {}
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        return {}
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except ValueError:
        return {}
    return {"behind": behind, "ahead": ahead}


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


def session_candidate_repo_roots(info: SessionInfo) -> list[str]:
    roots: list[str] = []
    candidates: list[str] = []
    candidates.extend(str(agent.cwd) for agent in info.agents if agent.cwd)
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    for value in candidates:
        repo = git_root_for_path(Path(value).expanduser())
        if repo and repo not in roots:
            roots.append(repo)
    return roots


def session_file_entry(
    session: str,
    agents: list[str],
    status: str,
    path: Path,
    repo: Path | None,
    source: str,
    added: int | None = None,
    removed: int | None = None,
    mtime: float | None = None,
) -> dict[str, Any]:
    rel_path = repo_relative_path(path, repo) if repo else None
    agent_list = [a for a in agents if a]
    return {
        "session": session,
        # C5: a changed file can be touched by 0, 1, or several agents, so carry the full list (the UI
        # renders 0-to-N icons from it). `agent` stays as a scalar first-agent alias for legacy consumers.
        "agents": agent_list,
        "agent": agent_list[0] if agent_list else "",
        "status": status,
        "repo": str(repo) if repo else "",
        "path": rel_path or str(path),
        "abs_path": str(path),
        "mtime": file_mtime(path) if mtime is None else mtime,
        "size": file_size(path),
        "source": source,
        "added": added,
        "removed": removed,
        "uploaded": is_generated_upload_name(path),
    }


def line_total(entries: list[dict[str, Any]], key: str) -> int:
    total = 0
    for entry in entries:
        value = entry.get(key)
        if isinstance(value, int):
            total += value
    return total


def session_files_payload_for_info(
    info: SessionInfo,
    hours: float = 24.0,
    now: float | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    # C6: `repo_refs` carries per-repo FROM/TO overrides ({repo_path: {"from","to"}}); a SHA chosen for
    # one repo no longer leaks into another. The scalar from_ref/to_ref stay as the global default applied
    # to any repo without an override (and drive the top-level payload refs for legacy single-repo callers).
    cutoff = (now if now is not None else time.time()) - bounded_session_files_hours(hours) * 3600
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    touched: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for agent in info.agents:
        if not agent.transcript:
            if agent.error:
                errors.append(agent.error)
            continue
        transcript = Path(agent.transcript).expanduser()
        transcript_mtime = file_mtime(transcript)
        if transcript_mtime < cutoff:
            continue
        for path_text, markers in scan_agent_changes(agent).items():
            # C5: accumulate every agent that touched this path instead of overwriting, so a file edited
            # by both Claude and Codex keeps both attributions (rendered as two icons).
            entry = touched.setdefault(path_text, {"agents": [], "status": "", "mtime": 0.0})
            if agent.kind and agent.kind not in entry["agents"]:
                entry["agents"].append(agent.kind)
            entry["status"] = classify_change(markers)
            entry["mtime"] = max(float(entry.get("mtime") or 0.0), transcript_mtime)

    repos: dict[str, set[str]] = {}
    for path_text, metadata in touched.items():
        path = Path(path_text)
        repo_text = git_root_for_path(path)
        if repo_text:
            repos.setdefault(repo_text, set()).add(path_text)
    for repo_text in session_candidate_repo_roots(info):
        repos.setdefault(repo_text, set())

    files: list[dict[str, Any]] = []
    repo_payloads: list[dict[str, Any]] = []
    refs_by_repo: dict[str, list[dict[str, str]]] = {}
    for repo_text in sorted(repos):
        repo = Path(repo_text)
        refs_by_repo[str(repo)] = git_recent_refs(repo)
        # C6: resolve this repo's effective FROM/TO — its own override if present, else the global scalar.
        repo_override = (repo_refs or {}).get(repo_text) or (repo_refs or {}).get(str(repo)) or {}
        repo_from = str(repo_override.get("from") or "").strip() or from_ref
        repo_to = str(repo_override.get("to") or "").strip() or to_ref
        repo_refs_active = refs_requested(repo_from, repo_to)
        sel_from, sel_to = diff_refs(repo_from, repo_to) if repo_refs_active else ("", "")
        repo_error = ""
        diff_base = "" if repo_refs_active else git_diff_base(repo)
        statuses, status_error = git_name_status(repo, diff_base or None, sel_from or None, sel_to or None)
        if status_error and repo_refs_active and diff_ref_resolution_error(status_error):
            # The requested ref is unknown in THIS repo (e.g. a SHA that only exists in another repo) —
            # fall back to this repo's own default base instead of erroring the whole payload.
            fallback_base = git_diff_base(repo)
            statuses, status_error = git_name_status(repo, fallback_base)
            numstat = git_numstat(repo, fallback_base) if not status_error else {}
            sel_from, sel_to = "", ""
            repo_error = "requested refs not found in this repo; showing default"
        elif status_error:
            errors.append(f"{repo.name}: {status_error}")
            repo_error = status_error
            statuses = {}
            numstat = {}
        else:
            numstat = git_numstat(repo, diff_base or None, sel_from or None, sel_to or None)
        touched_by_rel: dict[str, dict[str, Any]] = {}
        for touched_path, metadata in touched.items():
            rel_path = repo_relative_path(Path(touched_path), repo)
            if rel_path:
                touched_by_rel[rel_path] = metadata
        repo_entries: list[dict[str, Any]] = []
        for rel_path, status in statuses.items():
            path = (repo / rel_path).resolve(strict=False)
            counts = numstat.get(rel_path, {})
            added = counts.get("added")
            removed = counts.get("removed")
            if status in {"A", "?"} and rel_path not in numstat:
                added = untracked_added_line_count(path)
                removed = 0
            # C5: attribute the file to exactly the agents the transcripts say touched it — no fallback.
            # A repo-only change with no transcript attribution gets an empty list (zero agent icons).
            agents = touched_by_rel.get(rel_path, {}).get("agents", [])
            repo_entries.append(session_file_entry(info.session, agents, status, path, repo, "git", added, removed))
        for rel_path, metadata in touched_by_rel.items():
            if rel_path in statuses:
                continue
            path = (repo / rel_path).resolve(strict=False)
            repo_entries.append(session_file_entry(
                info.session,
                metadata.get("agents", []),
                "T",
                path,
                repo,
                "transcript",
                mtime=float(metadata.get("mtime") or 0.0),
            ))
        repo_entries.sort(key=lambda item: (-float(item.get("mtime") or 0), item["path"]))
        files.extend(repo_entries)
        repo_payload = {
            "repo": str(repo),
            "count": len(repo_entries),
            "touched_count": len(repos[repo_text]),
            "added": line_total(repo_entries, "added"),
            "removed": line_total(repo_entries, "removed"),
            # C6: report the refs THIS repo actually compared, plus any per-repo fallback, so each repo
            # header can render its own comparison title independently of the others.
            "from_ref": sel_from or "default",
            "to_ref": sel_to or "base",
            "error": repo_error,
        }
        repo_payload.update(git_ahead_behind(repo, sel_from or None, sel_to or None))
        repo_payloads.append(repo_payload)

    files.sort(key=lambda item: (-float(item.get("mtime") or 0), item["repo"], item["path"]))
    payload_from_ref = selected_from or "default"
    payload_to_ref = selected_to or "base"
    return {
        "session": info.session,
        "hours": bounded_session_files_hours(hours),
        "files": files,
        "repos": repo_payloads,
        "refs_by_repo": refs_by_repo,
        "from_ref": payload_from_ref,
        "to_ref": payload_to_ref,
        "errors": errors,
    }


def session_files_payload(
    session: str | None,
    infos: dict[str, SessionInfo],
    hours: float = 24.0,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, Any], HTTPStatus]:
    if session:
        info = infos.get(session)
        if info is None:
            return {"error": f"unknown session: {session}", "session": session}, HTTPStatus.NOT_FOUND
        payload = session_files_payload_for_info(info, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
        return payload, HTTPStatus.OK

    files: list[dict[str, Any]] = []
    repos: dict[str, dict[str, Any]] = {}
    refs_by_repo: dict[str, list[dict[str, str]]] = {}
    errors: list[str] = []
    for info in infos.values():
        payload = session_files_payload_for_info(info, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs)
        files.extend(payload["files"])
        errors.extend(payload["errors"])
        refs_by_repo.update(payload.get("refs_by_repo", {}))
        for repo in payload["repos"]:
            key = repo["repo"]
            existing = repos.setdefault(key, {"repo": key, "count": 0, "touched_count": 0, "added": 0, "removed": 0})
            existing["count"] += repo["count"]
            existing["touched_count"] += repo["touched_count"]
            existing["added"] += repo.get("added", 0)
            existing["removed"] += repo.get("removed", 0)
            # C6: carry the per-repo effective comparison refs/error from the first session that touched it.
            existing.setdefault("from_ref", repo.get("from_ref", "default"))
            existing.setdefault("to_ref", repo.get("to_ref", "base"))
            existing.setdefault("error", repo.get("error", ""))
    files.sort(key=lambda item: (-float(item.get("mtime") or 0), item["session"], item["path"]))
    return {
        "session": "",
        "hours": bounded_session_files_hours(hours),
        "files": files,
        "repos": sorted(repos.values(), key=lambda item: item["repo"]),
        "refs_by_repo": refs_by_repo,
        "from_ref": diff_refs(from_ref, to_ref)[0] if refs_requested(from_ref, to_ref) else "default",
        "to_ref": diff_refs(from_ref, to_ref)[1] if refs_requested(from_ref, to_ref) else "base",
        "errors": errors,
    }, HTTPStatus.OK

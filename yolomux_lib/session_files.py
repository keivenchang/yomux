# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Repo-aware AI file-change attribution for live sessions."""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .common import AgentInfo
from .common import SessionInfo
from .common import git
from .common import git_ahead_behind_counts
from .common import is_generated_upload_name
from .filesystem import git_root_for_path
from .sessions import find_recent_codex_transcript
from .sessions import recent_codex_transcript_candidates
from .types import RepoPayload
from .types import SessionFileEntry
from .types import SessionFilesPayload
from .workdir import session_workdir


CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CODEX_PATCH_RE = re.compile(r"\*\*\* (Add|Update|Delete) File: ([^\"\\\n]+)")
CODEX_PATCH_STATUS = {"Add": "A", "Update": "M", "Delete": "D"}
CODEX_SHELL_TOOL_NAMES = {"exec_command", "shell_command", "shell"}
SHELL_COMMAND_BREAK_TOKENS = {"&&", "||", ";", "|"}
SHELL_RUNNERS = {"bash", "sh", "zsh"}
SESSION_FILES_MAX_HOURS = 24 * 14
SESSION_FILES_CUTOFF_GRACE_SECONDS = 60.0
_CODEX_TRANSCRIPT_SCAN_CACHE_MAX = 64
_CODEX_TRANSCRIPT_SCAN_CACHE: dict[tuple[str, str, bool, float, int], dict[str, set[str]]] = {}
_CLAUDE_TRANSCRIPT_SCAN_CACHE_MAX = 64
_CLAUDE_TRANSCRIPT_SCAN_CACHE: dict[tuple[str, str, float, int], dict[str, set[str]]] = {}


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
    return Path(os.path.abspath(os.fspath(path)))


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


def file_mtime_or_fallback(path: Path, fallback: Any = 0.0) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        pass
    try:
        return float(fallback) if fallback not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def scan_claude_transcript(path: Path, cwd: str | None = None) -> dict[str, set[str]]:
    # Same (path, mtime, size) memoization the codex scanner uses: candidate_session_cwds now scans
    # transcripts on the hot metadata-refresh path, so an unchanged transcript must read from disk once.
    cache_key = claude_transcript_scan_cache_key(path, cwd)
    if cache_key is not None:
        cached = _CLAUDE_TRANSCRIPT_SCAN_CACHE.get(cache_key)
        if cached is not None:
            return copy_change_set(cached)
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
    if cache_key is not None:
        if len(_CLAUDE_TRANSCRIPT_SCAN_CACHE) >= _CLAUDE_TRANSCRIPT_SCAN_CACHE_MAX:
            _CLAUDE_TRANSCRIPT_SCAN_CACHE.pop(next(iter(_CLAUDE_TRANSCRIPT_SCAN_CACHE)))
        _CLAUDE_TRANSCRIPT_SCAN_CACHE[cache_key] = copy_change_set(changes)
    return changes


def claude_transcript_scan_cache_key(path: Path, cwd: str | None) -> tuple[str, str, float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.expanduser().resolve(strict=False)), str(cwd or ""), stat.st_mtime, stat.st_size)


def scan_codex_transcript(path: Path, cwd: str | None = None, include_patch_text: bool = True) -> dict[str, set[str]]:
    cache_key = codex_transcript_scan_cache_key(path, cwd, include_patch_text)
    if cache_key is not None:
        cached = _CODEX_TRANSCRIPT_SCAN_CACHE.get(cache_key)
        if cached is not None:
            return copy_change_set(cached)
    changes: dict[str, set[str]] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if include_patch_text:
                    for verb, raw_path in CODEX_PATCH_RE.findall(line):
                        resolved = resolved_change_path(raw_path, cwd)
                        if resolved is None:
                            continue
                        changes.setdefault(str(resolved), set()).add(CODEX_PATCH_STATUS[verb])
                if not codex_line_may_contain_git_change(line):
                    continue
                for path_text, markers in scan_codex_tool_call_changes(line, cwd).items():
                    changes.setdefault(path_text, set()).update(markers)
    except OSError:
        return changes
    if cache_key is not None:
        if len(_CODEX_TRANSCRIPT_SCAN_CACHE) >= _CODEX_TRANSCRIPT_SCAN_CACHE_MAX:
            oldest_key = next(iter(_CODEX_TRANSCRIPT_SCAN_CACHE), None)
            if oldest_key is not None:
                _CODEX_TRANSCRIPT_SCAN_CACHE.pop(oldest_key, None)
        _CODEX_TRANSCRIPT_SCAN_CACHE[cache_key] = copy_change_set(changes)
    return changes


def codex_transcript_scan_cache_key(path: Path, cwd: str | None, include_patch_text: bool) -> tuple[str, str, bool, float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.expanduser().resolve(strict=False)), str(cwd or ""), bool(include_patch_text), stat.st_mtime, stat.st_size)


def copy_change_set(changes: dict[str, set[str]]) -> dict[str, set[str]]:
    return {path_text: set(markers) for path_text, markers in changes.items()}


def codex_line_may_contain_git_change(line: str) -> bool:
    if "git" not in line:
        return False
    return any(token in line for token in (" add ", " rm ", " mv ", " add\\", " rm\\", " mv\\"))


def codex_tool_call_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str) or not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def scan_codex_tool_call_changes(line: str, cwd: str | None = None) -> dict[str, set[str]]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return {}
    if not isinstance(record, dict):
        return {}
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("type") or "") not in {"function_call", "custom_tool_call"}:
        return {}
    if str(payload.get("name") or "") not in CODEX_SHELL_TOOL_NAMES:
        return {}
    arguments = codex_tool_call_arguments(payload)
    command = arguments.get("cmd") or arguments.get("command")
    if not isinstance(command, str):
        return {}
    workdir = arguments.get("workdir")
    effective_cwd = workdir if isinstance(workdir, str) and workdir else cwd
    return scan_shell_command_changes(command, effective_cwd)


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return []


def scan_shell_command_changes(command: str, cwd: str | None = None) -> dict[str, set[str]]:
    tokens = shell_tokens(command)
    if not tokens:
        return {}
    changes: dict[str, set[str]] = {}
    effective_cwd = cwd
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token in SHELL_COMMAND_BREAK_TOKENS:
            segment_changes, effective_cwd = scan_shell_command_segment_changes(segment, effective_cwd)
            for path_text, markers in segment_changes.items():
                changes.setdefault(path_text, set()).update(markers)
            segment = []
            continue
        segment.append(token)
    return changes


def scan_shell_command_segment_changes(tokens: list[str], cwd: str | None = None) -> tuple[dict[str, set[str]], str | None]:
    if not tokens:
        return {}, cwd
    if tokens[0] == "cd" and len(tokens) >= 2:
        resolved = resolved_change_path(tokens[1], cwd)
        return {}, str(resolved) if resolved is not None else cwd
    inline_command = shell_runner_inline_command(tokens)
    if inline_command is not None:
        return scan_shell_command_changes(inline_command, cwd), cwd
    return scan_git_command_changes(tokens, cwd), cwd


def shell_runner_inline_command(tokens: list[str]) -> str | None:
    if not tokens or Path(tokens[0]).name not in SHELL_RUNNERS:
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if token == "-c" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("-") and "c" in token and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def scan_git_command_changes(tokens: list[str], cwd: str | None = None) -> dict[str, set[str]]:
    if not tokens or tokens[0] != "git":
        return {}
    index = 1
    effective_cwd = cwd
    while index < len(tokens):
        token = tokens[index]
        if token == "-C" and index + 1 < len(tokens):
            resolved = resolved_change_path(tokens[index + 1], effective_cwd)
            effective_cwd = str(resolved) if resolved is not None else tokens[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(tokens):
        return {}
    subcommand = tokens[index]
    if subcommand == "add":
        return scan_git_path_args(tokens[index + 1 :], effective_cwd, "M")
    if subcommand == "rm":
        return scan_git_path_args(tokens[index + 1 :], effective_cwd, "D")
    if subcommand == "mv":
        path_args = git_path_args(tokens[index + 1 :])
        changes: dict[str, set[str]] = {}
        if len(path_args) >= 1:
            resolved = resolved_change_path(path_args[0], effective_cwd)
            if resolved is not None:
                changes.setdefault(str(resolved), set()).add("D")
        if len(path_args) >= 2:
            resolved = resolved_change_path(path_args[-1], effective_cwd)
            if resolved is not None:
                changes.setdefault(str(resolved), set()).add("A")
        return changes
    return {}


def scan_git_path_args(tokens: list[str], cwd: str | None, marker: str) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = {}
    for raw_path in git_path_args(tokens):
        resolved = resolved_change_path(raw_path, cwd)
        if resolved is None:
            continue
        changes.setdefault(str(resolved), set()).add(marker)
    return changes


def git_path_args(tokens: list[str]) -> list[str]:
    paths: list[str] = []
    index = 0
    positional = False
    options_with_value = {"--pathspec-from-file", "--chmod"}
    while index < len(tokens):
        token = tokens[index]
        if positional:
            paths.append(token)
            index += 1
            continue
        if token == "--":
            positional = True
            index += 1
            continue
        if token in SHELL_COMMAND_BREAK_TOKENS:
            break
        if token in options_with_value:
            index += 2
            continue
        if token.startswith("--pathspec-from-file=") or token.startswith("--chmod="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        paths.append(token)
        index += 1
    return paths


def scan_agent_changes(agent: AgentInfo) -> dict[str, set[str]]:
    if not agent.transcript:
        return {}
    path = Path(agent.transcript).expanduser()
    if agent.kind == "claude":
        return scan_claude_transcript(path, agent.cwd)
    if agent.kind == "codex":
        return scan_codex_transcript(path, agent.cwd)
    return {}


def session_touched_dirs(info: SessionInfo) -> list[str]:
    """Directories the session's agents have actually EDITED files in, derived from each agent's
    transcript (the same edit-tool scan the Modified-files / Tabber panes use, so it counts edits, not
    reads). This is the signal that lets repo detection find the real project repo even when the live
    pane cwd is $HOME or another non-repo: a claude launched from ~ but editing files in
    ~/yolomux.dev8003 still surfaces that repo. Returns unique containing directories in first-seen
    order; git-root resolution and dedupe across repos happen in the caller's repo_summary pass."""
    dirs: list[str] = []
    seen: set[str] = set()
    for agent in info.agents:
        for path_text in scan_agent_changes(agent):
            parent = str(Path(path_text).parent)
            if parent and parent != "." and parent not in seen:
                seen.add(parent)
                dirs.append(parent)
    return dirs


def bounded_session_files_hours(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 24.0
    return max(0.25, min(parsed, float(SESSION_FILES_MAX_HOURS)))


def session_files_cutoff(hours: float, now: float | None = None) -> float:
    # Poll ticks near the lookback boundary should not make a repo appear on one refresh and vanish on the
    # next just because transcript mtime crossed the exact second cutoff.
    current = now if now is not None else time.time()
    return current - bounded_session_files_hours(hours) * 3600 - SESSION_FILES_CUTOFF_GRACE_SECONDS


def git_default_branch_ref(repo: Path) -> str | None:
    result = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd=str(repo), timeout=5.0)
    if result.returncode == 0:
        ref = result.stdout.strip()
        if ref:
            return ref
    for ref in ("origin/main", "origin/master", "main", "master"):
        verify_ref = f"refs/remotes/{ref}" if ref.startswith("origin/") else f"refs/heads/{ref}"
        verify = git(["show-ref", "--verify", "--quiet", verify_ref], cwd=str(repo), timeout=5.0)
        if verify.returncode == 0:
            return ref
    return None


def git_diff_base(repo: Path) -> str:
    default_ref = git_default_branch_ref(repo)
    if default_ref:
        result = git(["merge-base", default_ref, "HEAD"], cwd=str(repo), timeout=5.0)
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
    result = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=str(repo), timeout=3.0)
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
    order = git(["merge-base", "--is-ancestor", from_ref, to_ref], cwd=str(repo), timeout=5.0)
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


def git_decoration_aliases(decorations: str, *, include_head: bool = False) -> list[str]:
    aliases: list[str] = ["HEAD"] if include_head else []
    for raw in decorations.split(","):
        for part in raw.strip().split(" -> "):
            alias = part.strip()
            if alias.startswith("tag: "):
                alias = alias.removeprefix("tag: ").strip()
            if alias.startswith("refs/heads/"):
                alias = alias.removeprefix("refs/heads/")
            elif alias.startswith("refs/remotes/"):
                alias = alias.removeprefix("refs/remotes/")
            if alias == "HEAD" and not include_head:
                continue
            if not alias or alias == "origin/HEAD" or alias in aliases:
                continue
            aliases.append(alias)

    def sort_key(alias: str) -> tuple[int, str]:
        if alias == "HEAD":
            return (0, alias)
        if alias in {"origin/main", "origin/master"}:
            return (1, alias)
        if alias in {"main", "master"}:
            return (2, alias)
        if alias.startswith("origin/"):
            return (3, alias)
        return (4, alias)

    return sorted(aliases, key=sort_key)


def git_ref_label(short_sha: str, aliases: list[str]) -> str:
    if not aliases:
        return short_sha
    return f"{short_sha}/{aliases[0]}{' ' + ' '.join(aliases[1:]) if len(aliases) > 1 else ''}"


def git_recent_refs(repo: Path, limit: int = 100) -> list[dict[str, Any]]:
    result = git([
        "log",
        "--decorate=short",
        f"--max-count={max(1, min(limit, 200))}",
        "--pretty=format:%H%x1f%h%x1f%s%x1f%at%x1f%an%x1f%D",
    ], cwd=str(repo), timeout=5.0)
    refs: list[dict[str, Any]] = [{"ref": "HEAD", "short": "HEAD", "subject": "base commit"}, {"ref": "current", "short": "current", "subject": "working tree"}]
    if result.returncode != 0:
        return refs
    seen = {"current", "HEAD"}
    head_commit_seen = False
    for line in result.stdout.splitlines():
        parts = line.split("\x1f", 5)
        if len(parts) < 3 or not parts[0] or parts[0] in seen:
            continue
        aliases = git_decoration_aliases(parts[5] if len(parts) >= 6 else "")
        entry: dict[str, Any] = {"ref": parts[0], "short": git_ref_label(parts[1], aliases), "subject": parts[2]}
        if aliases:
            entry["aliases"] = aliases
        if len(parts) >= 5:
            entry["date"] = parts[3]
            entry["author"] = parts[4]
        if not head_commit_seen:
            head_aliases = git_decoration_aliases(parts[5] if len(parts) >= 6 else "", include_head=True)
            refs[0] = {
                **refs[0],
                "short": git_ref_label(parts[1], head_aliases),
                "subject": parts[2],
                "commit": parts[0],
                "aliases": head_aliases,
            }
            if len(parts) >= 5:
                refs[0]["date"] = parts[3]
                refs[0]["author"] = parts[4]
            head_commit_seen = True
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
    diff = git(["diff", "--name-status", "-z", "--find-renames", *diff_args], cwd=str(repo), timeout=5.0)
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
        untracked = git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=str(repo), timeout=5.0)
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
    diff = git(["diff", "--numstat", "-z", "--find-renames", *diff_args], cwd=str(repo), timeout=5.0)
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
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=str(repo), timeout=3.0)
        if upstream.returncode != 0 or not upstream.stdout.strip():
            return {}
        left_ref = upstream.stdout.strip()
        right_ref = "HEAD"
    if not git_ref_exists(repo, left_ref) or not git_ref_exists(repo, right_ref):
        return {}
    # shared ahead/behind parse. left...right -> ahead = right-only commits, behind = left-only.
    counts = git_ahead_behind_counts(str(repo), left_ref, right_ref)
    if counts is None:
        return {}
    ahead, behind = counts
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


def configured_session_repo_candidate(info: SessionInfo) -> str | None:
    configured = session_workdir(info.session)
    if not configured.is_dir():
        return None
    try:
        if configured.resolve() == Path.home().resolve():
            return None
    except OSError:
        pass
    return str(configured)


def session_candidate_repo_roots(info: SessionInfo) -> list[str]:
    roots: list[str] = []
    candidates: list[str] = []
    candidates.extend(str(agent.cwd) for agent in info.agents if agent.cwd)
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    configured = configured_session_repo_candidate(info)
    if configured:
        candidates.append(configured)
    for value in candidates:
        repo = git_root_for_path(Path(value).expanduser())
        if repo and repo not in roots:
            roots.append(repo)
    return roots


def session_live_pane_repo_roots(info: SessionInfo) -> list[str]:
    roots: list[str] = []
    candidates: list[str] = []
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    configured = configured_session_repo_candidate(info)
    if configured:
        candidates.append(configured)
    for value in candidates:
        repo = git_root_for_path(Path(value).expanduser())
        if repo and repo not in roots:
            roots.append(repo)
    return roots


def refreshing_session_files_payload_for_info(
    info: SessionInfo,
    hours: float = 24.0,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
) -> SessionFilesPayload:
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    repo_payloads: list[RepoPayload] = []
    for repo_text in session_live_pane_repo_roots(info):
        repo = Path(repo_text)
        repo_override = (repo_refs or {}).get(repo_text) or (repo_refs or {}).get(str(repo)) or {}
        repo_from = str(repo_override.get("from") or "").strip() or from_ref
        repo_to = str(repo_override.get("to") or "").strip() or to_ref
        repo_refs_active = refs_requested(repo_from, repo_to)
        sel_from, sel_to = diff_refs(repo_from, repo_to) if repo_refs_active else ("", "")
        repo_payload: RepoPayload = {
            "repo": str(repo),
            "count": 0,
            "touched_count": 0,
            "added": 0,
            "removed": 0,
            "from_ref": sel_from or "default",
            "to_ref": sel_to or "base",
            "error": "",
        }
        repo_payload.update(git_ahead_behind(repo, sel_from or None, sel_to or None))
        repo_payloads.append(repo_payload)
    return {
        "session": info.session,
        "hours": bounded_session_files_hours(hours),
        "files": [],
        "repos": repo_payloads,
        "refs_by_repo": {},
        "from_ref": selected_from or "default",
        "to_ref": selected_to or "base",
        "errors": [],
        "warnings": [],
        "refreshing_elsewhere": True,
    }


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
    diff_tracked: bool | None = None,
    agent_windows: list[dict[str, Any]] | None = None,
) -> SessionFileEntry:
    rel_path = repo_relative_path(path, repo) if repo else None
    agent_list = [a for a in agents if a]
    tracked_diff = bool(repo) and source == "git" and status != "?"
    missing = not path.exists()
    if diff_tracked is not None:
        tracked_diff = diff_tracked
    return {
        "session": session,
        # C5: a changed file can be touched by 0, 1, or several agents, so carry the full list (the UI
        # renders 0-to-N icons from it). `agent` stays as a scalar first-agent alias for legacy consumers.
        "agents": agent_list,
        "agent": agent_list[0] if agent_list else "",
        "agent_windows": agent_windows or [],
        "status": status,
        "repo": str(repo) if repo else "",
        "path": rel_path or str(path),
        "abs_path": str(path),
        "mtime": file_mtime(path) if mtime is None else mtime,
        "size": file_size(path),
        "missing": missing,
        "source": source,
        "added": added,
        "removed": removed,
        "diff_tracked": tracked_diff,
        "uploaded": is_generated_upload_name(path),
    }


def line_total(entries: list[dict[str, Any]], key: str) -> int:
    total = 0
    for entry in entries:
        if entry.get("diff_tracked") is not True:
            continue
        value = entry.get(key)
        if isinstance(value, int):
            total += value
    return total


def differ_visible_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if str(entry.get("status") or "M").upper() != "T"]


def merge_agent_lists(*agent_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for agent_list in agent_lists:
        for agent_name in agent_list:
            if agent_name and agent_name not in merged:
                merged.append(agent_name)
    return merged


def agent_window_for_info(info: SessionInfo, agent: AgentInfo) -> tuple[str, str]:
    for pane in info.panes:
        if pane.target == agent.pane_target or pane.pane_id == agent.pane_target:
            return str(pane.window or ""), str(pane.pane or "")
    match = re.match(r"^[^:]+:(?P<window>[^.]+)(?:\.(?P<pane>.*))?$", str(agent.pane_target or ""))
    if not match:
        return "", ""
    return match.group("window") or "", match.group("pane") or ""


def agent_window_attribution(info: SessionInfo, agent: AgentInfo) -> dict[str, Any]:
    window, pane = agent_window_for_info(info, agent)
    try:
        window_index: int | None = int(window)
    except ValueError:
        window_index = None
    return {
        "kind": str(agent.kind or ""),
        "window": window,
        "window_index": window_index,
        "pane": pane,
        "pane_target": str(agent.pane_target or ""),
    }


def merge_agent_window_lists(*window_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for window_list in window_lists:
        for raw in window_list:
            if not isinstance(raw, dict):
                continue
            item = {
                "kind": str(raw.get("kind") or ""),
                "window": str(raw.get("window") or ""),
                "window_index": raw.get("window_index") if isinstance(raw.get("window_index"), int) else None,
                "pane": str(raw.get("pane") or ""),
                "pane_target": str(raw.get("pane_target") or ""),
            }
            key = (item["kind"], item["window"], item["pane"], item["pane_target"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def touched_files_for_info(info: SessionInfo, cutoff: float, warnings: list[str] | None = None) -> dict[str, dict[str, Any]]:
    touched: dict[str, dict[str, Any]] = {}
    for agent in info.agents:
        if not agent.transcript:
            # D2: a missing/undiscoverable transcript is inherently a PER-AGENT condition (e.g. an inactive
            # background Codex pane that never wrote a discoverable rollout). It is NOT a session-level
            # failure: the other agents and git-derived repo data in the same session are still valid. Surface
            # it as a non-blocking warning so the Differ keeps rendering the changed files/repos instead of
            # treating the whole session as failed (the frontend renders payload["errors"] as red blocking
            # rows; warnings are a separate, non-blocking channel).
            if agent.error and warnings is not None:
                warnings.append(agent.error)
            continue
        transcript = Path(agent.transcript).expanduser()
        transcript_mtime = file_mtime(transcript)
        if transcript_mtime < cutoff:
            continue
        for path_text, markers in scan_agent_changes(agent).items():
            # C5: accumulate every agent that touched this path instead of overwriting, so a file edited
            # by both Claude and Codex keeps both attributions (rendered as two icons).
            entry = touched.setdefault(path_text, {"agents": [], "agent_windows": [], "status": "", "mtime": 0.0})
            if agent.kind and agent.kind not in entry["agents"]:
                entry["agents"].append(agent.kind)
            entry["agent_windows"] = merge_agent_window_lists(entry.get("agent_windows", []), [agent_window_attribution(info, agent)])
            entry["status"] = classify_change(markers)
            entry["mtime"] = max(float(entry.get("mtime") or 0.0), transcript_mtime)
    for path_text, metadata in historical_codex_changes_for_info(info, cutoff).items():
        entry = touched.setdefault(path_text, {"agents": [], "agent_windows": [], "status": "", "mtime": 0.0})
        if "codex" not in entry["agents"]:
            entry["agents"].append("codex")
        entry["status"] = str(metadata.get("status") or "M")
        entry["mtime"] = max(float(entry.get("mtime") or 0.0), float(metadata.get("mtime") or 0.0))
    return touched


def historical_codex_changes_for_info(info: SessionInfo, cutoff: float) -> dict[str, dict[str, Any]]:
    seen = {str(agent.transcript) for agent in info.agents if agent.transcript}
    changes: dict[str, dict[str, Any]] = {}
    for cwd in historical_codex_candidate_cwds(info):
        transcript = historical_codex_transcript_for_cwd(cwd, cutoff)
        if transcript is None:
            continue
        transcript_text = str(transcript)
        if transcript_text in seen or file_mtime(transcript) < cutoff:
            continue
        seen.add(transcript_text)
        transcript_mtime = file_mtime(transcript)
        for path_text, markers in scan_codex_transcript(transcript, cwd, include_patch_text=False).items():
            if not path_is_under_text(path_text, cwd):
                continue
            entry = changes.setdefault(path_text, {"status": "", "mtime": 0.0})
            entry["status"] = classify_change(markers)
            entry["mtime"] = max(float(entry.get("mtime") or 0.0), transcript_mtime)
    return changes


def historical_codex_transcript_for_cwd(cwd: str, cutoff: float) -> Path | None:
    candidates: list[Path] = []
    direct = find_recent_codex_transcript(cwd)
    if direct is not None:
        candidates.append(direct)
    candidates.extend(recent_codex_transcript_candidates())
    seen: set[str] = set()
    for transcript in candidates:
        transcript_text = str(transcript)
        if transcript_text in seen or file_mtime(transcript) < cutoff:
            continue
        seen.add(transcript_text)
        changes = scan_codex_transcript(transcript, cwd, include_patch_text=False)
        if any(path_is_under_text(path_text, cwd) for path_text in changes):
            return transcript
    return None


def path_is_under_text(path_text: str, root_text: str) -> bool:
    path = Path(path_text).expanduser().resolve(strict=False)
    root = Path(root_text).expanduser().resolve(strict=False)
    return path == root or path.is_relative_to(root)


def historical_codex_candidate_cwds(info: SessionInfo) -> list[str]:
    candidates: list[str] = []
    for agent in info.agents:
        if agent.cwd:
            candidates.append(agent.cwd)
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    candidates.extend(session_candidate_repo_roots(info))
    unique: list[str] = []
    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        repo = git_root_for_path(Path(text).expanduser())
        if repo and repo not in unique:
            unique.append(repo)
    return unique


def agent_attribution_by_path(infos: dict[str, SessionInfo], cutoff: float) -> dict[str, list[str]]:
    attribution: dict[str, list[str]] = {}
    for info in infos.values():
        for path_text, metadata in touched_files_for_info(info, cutoff).items():
            attribution[path_text] = merge_agent_lists(attribution.get(path_text, []), metadata.get("agents", []))
    return attribution


def session_files_payload_for_info(
    info: SessionInfo,
    hours: float = 24.0,
    now: float | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
    agent_attribution: dict[str, list[str]] | None = None,
) -> SessionFilesPayload:
    # C6: `repo_refs` carries per-repo FROM/TO overrides ({repo_path: {"from","to"}}); a SHA chosen for
    # one repo no longer leaks into another. The scalar from_ref/to_ref stay as the global default applied
    # to any repo without an override (and drive the top-level payload refs for legacy single-repo callers).
    cutoff = session_files_cutoff(hours, now)
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    errors: list[str] = []
    # D2: per-agent transcript-discovery problems land here, separate from the blocking `errors` list, so a
    # single inactive agent's missing transcript does not read as a session-level Differ failure.
    warnings: list[str] = []
    touched = touched_files_for_info(info, cutoff, warnings)

    repos: dict[str, set[str]] = {}
    outside_repo_paths: set[str] = set()
    for path_text, metadata in touched.items():
        path = Path(path_text)
        repo_text = git_root_for_path(path)
        if repo_text:
            repos.setdefault(repo_text, set()).add(path_text)
        else:
            outside_repo_paths.add(path_text)
    candidate_repo_roots = set(session_candidate_repo_roots(info))
    for repo_text in candidate_repo_roots:
        repos.setdefault(repo_text, set())
    live_pane_repo_roots = set(session_live_pane_repo_roots(info))

    files: list[SessionFileEntry] = []
    repo_payloads: list[RepoPayload] = []
    refs_by_repo: dict[str, list[dict[str, Any]]] = {}
    for repo_text in sorted(repos):
        repo = Path(repo_text)
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
        repo_entries: list[SessionFileEntry] = []
        for rel_path, status in statuses.items():
            path = repo / rel_path
            counts = numstat.get(rel_path, {})
            added = counts.get("added")
            removed = counts.get("removed")
            diff_tracked = status != "?" and rel_path in numstat
            if status in {"A", "?"} and rel_path not in numstat:
                added = untracked_added_line_count(path)
                removed = 0
            # C5: attribute the file to exactly the agents the transcripts say touched it — no fallback.
            # A repo-only change with no transcript attribution gets an empty list (zero agent icons).
            agents = merge_agent_lists(touched_by_rel.get(rel_path, {}).get("agents", []), (agent_attribution or {}).get(str(path), []))
            repo_entries.append(session_file_entry(
                info.session,
                agents,
                status,
                path,
                repo,
                "git",
                added=added,
                removed=removed,
                diff_tracked=diff_tracked,
                agent_windows=merge_agent_window_lists(touched_by_rel.get(rel_path, {}).get("agent_windows", [])),
            ))
        for rel_path, metadata in touched_by_rel.items():
            if rel_path in statuses:
                continue
            if repo_refs_active:
                continue
            path = repo / rel_path
            repo_entries.append(session_file_entry(
                info.session,
                merge_agent_lists(metadata.get("agents", []), (agent_attribution or {}).get(str(path), [])),
                "T",
                path,
                repo,
                "transcript",
                mtime=file_mtime_or_fallback(path, metadata.get("mtime")),
                agent_windows=merge_agent_window_lists(metadata.get("agent_windows", [])),
            ))
        repo_entries.sort(key=lambda item: (-float(item.get("mtime") or 0), item["path"]))
        files.extend(repo_entries)
        rendered_entries = differ_visible_entries(repo_entries)
        if not rendered_entries and repo_text not in live_pane_repo_roots and not repo_refs_active:
            continue
        refs_by_repo[str(repo)] = git_recent_refs(repo)
        repo_payload: RepoPayload = {
            "repo": str(repo),
            "count": len(rendered_entries),
            "touched_count": len(repos[repo_text]),
            "added": line_total(rendered_entries, "added"),
            "removed": line_total(rendered_entries, "removed"),
            # C6: report the refs THIS repo actually compared, plus any per-repo fallback, so each repo
            # header can render its own comparison title independently of the others.
            "from_ref": sel_from or "default",
            "to_ref": sel_to or "base",
            "error": repo_error,
        }
        repo_payload.update(git_ahead_behind(repo, sel_from or None, sel_to or None))
        repo_payloads.append(repo_payload)

    outside_entries: list[SessionFileEntry] = []
    if outside_repo_paths and not refs_active:
        for path_text in sorted(outside_repo_paths):
            path = Path(path_text)
            metadata = touched.get(path_text, {})
            status = str(metadata.get("status") or "?")
            outside_entries.append(session_file_entry(
                info.session,
                merge_agent_lists(metadata.get("agents", []), (agent_attribution or {}).get(str(path), [])),
                status if status in {"A", "D", "M", "?"} else "?",
                path,
                None,
                "transcript",
                untracked_added_line_count(path) if path.exists() and path.is_file() else None,
                0 if path.exists() and path.is_file() else None,
                mtime=file_mtime_or_fallback(path, metadata.get("mtime")),
                diff_tracked=False,
                agent_windows=merge_agent_window_lists(metadata.get("agent_windows", [])),
            ))
        outside_entries.sort(key=lambda item: (-float(item.get("mtime") or 0), item["path"]))
        files.extend(outside_entries)
        repo_payloads.append({
            "repo": "",
            "count": len(outside_entries),
            "touched_count": len(outside_entries),
            "added": 0,
            "removed": 0,
            "from_ref": "default",
            "to_ref": "base",
            "error": "",
        })

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
        "warnings": warnings,
    }


def session_files_payload(
    session: str | None,
    infos: dict[str, SessionInfo],
    hours: float = 24.0,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
    include_cross_session_attribution: bool = True,
) -> tuple[SessionFilesPayload, HTTPStatus]:
    now = time.time()
    cutoff = session_files_cutoff(hours, now)
    attribution = agent_attribution_by_path(infos, cutoff) if include_cross_session_attribution else {}
    if session:
        info = infos.get(session)
        if info is None:
            return {"error": f"unknown session: {session}", "session": session}, HTTPStatus.NOT_FOUND
        payload = session_files_payload_for_info(info, hours, now=now, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, agent_attribution=attribution)
        return payload, HTTPStatus.OK

    files: list[SessionFileEntry] = []
    repos: dict[str, RepoPayload] = {}
    refs_by_repo: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for info in infos.values():
        payload = session_files_payload_for_info(info, hours, now=now, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, agent_attribution=attribution)
        files.extend(payload["files"])
        errors.extend(payload["errors"])
        warnings.extend(payload.get("warnings", []))
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
        "warnings": warnings,
    }, HTTPStatus.OK

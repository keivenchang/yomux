from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

from .common import AgentInfo
from .common import MAIN_BRANCHES
from .common import METADATA_CACHE_TTL_SECONDS
from .common import OTHER_BRANCH_LIMIT
from .common import PaneInfo
from .common import SessionInfo
from .common import _CACHE_MISS
from .common import git
from .github_client import extract_linear_ids
from .github_client import github_checks_unknown
from .github_client import github_pull_request_by_branch
from .github_client import github_pull_request_by_number
from .github_client import github_pull_request_url
from .github_client import pull_request_status_label  # noqa: F401 - re-exported for existing metadata callers
from .github_client import summarize_github_checks  # noqa: F401 - re-exported for existing metadata callers
from .linear_client import linear_issue_metadata
from .workdir import numbered_session_workdir
from .workdir import session_workdir


def project_inventory(sessions: dict[str, SessionInfo], current_session: str) -> tuple[str | None, list[dict[str, Any]]]:
    focus_root = focus_root_for_session(current_session)
    inventory: list[dict[str, Any]] = []
    for session, info in sorted(sessions.items()):
        if focus_root is None and session != current_session:
            continue
        selected = info.selected_pane
        cwd = focused_cwd(info, focus_root, current=session == current_session)
        if focus_root and cwd is None:
            continue
        entry: dict[str, Any] = {
            "session": session,
            "current": session == current_session,
            "cwd": cwd,
            "pane": pane_inventory(selected, focus_root),
            "agents": [agent_inventory(item) for item in info.agents],
            "git": git_inventory(cwd),
        }
        inventory.append(entry)
    return focus_root, inventory

def focus_root_for_session(session: str) -> str | None:
    workdir = session_workdir(session)
    if workdir.is_dir() and workdir.resolve() != Path.home().resolve():
        return str(workdir.resolve())
    return None

def focused_cwd(info: SessionInfo, focus_root: str | None, current: bool) -> str | None:
    if current and focus_root:
        return focus_root
    paths: list[str] = []
    paths.extend(agent.cwd for agent in info.agents if agent.cwd)
    paths.extend(pane.current_path for pane in info.panes if pane.current_path)
    for path in paths:
        if not focus_root or path_within(path, focus_root):
            return path
    return None

def pane_inventory(pane: PaneInfo | None, focus_root: str | None) -> dict[str, Any] | None:
    if pane is None:
        return None
    current_path = pane.current_path if not focus_root or path_within(pane.current_path, focus_root) else None
    return {
        "target": pane.target,
        "current_path": current_path,
        "command": pane.command,
        "active": pane.active,
        "title": pane.title,
    }

def agent_inventory(agent: AgentInfo) -> dict[str, Any]:
    return {
        "kind": agent.kind,
        "pid": agent.pid,
        "pane_target": agent.pane_target,
        "status": agent.status,
        "error": agent.error,
    }

def path_within(path_text: str, root_text: str) -> bool:
    try:
        path = Path(path_text).expanduser().resolve()
        root = Path(root_text).expanduser().resolve()
    except OSError:
        return False
    return path == root or path.is_relative_to(root)

def git_inventory(cwd: str | None) -> dict[str, Any] | None:
    if not cwd:
        return None
    root = git(["rev-parse", "--show-toplevel"], cwd)
    if root.returncode != 0:
        return None
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    head_sha = git(["rev-parse", "HEAD"], cwd)
    head = git(["log", "-1", "--pretty=%h %s"], cwd)
    upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd)
    status = git(["status", "--short"], cwd)
    origin_url = git(["config", "--get", "remote.origin.url"], cwd)
    upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else None
    branch_name = branch.stdout.strip() if branch.returncode == 0 else None
    ahead, behind = git_ahead_behind(cwd, upstream_name)
    status_lines = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
    return {
        "root": root.stdout.strip(),
        "branch": branch_name,
        "upstream": upstream_name,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "head_sha": head_sha.stdout.strip() if head_sha.returncode == 0 else None,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(status_lines),
        "status": status_lines[:30],
        "github_repo": parse_github_remote(origin_url.stdout.strip()) if origin_url.returncode == 0 else None,
        "other_branches": local_branch_inventory(cwd, branch_name),
    }

def git_ahead_behind(cwd: str, upstream: str | None) -> tuple[int | None, int | None]:
    if not upstream:
        return None, None
    result = git(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"], cwd)
    if result.returncode != 0:
        return None, None
    parts = result.stdout.split()
    if len(parts) != 2:
        return None, None
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except ValueError:
        return None, None
    return ahead, behind

def local_branch_inventory(cwd: str, current_branch: str | None) -> dict[str, Any]:
    result = git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)\t%(objectname)\t%(committerdate:unix)\t%(committerdate:relative)\t%(subject)",
            "refs/heads",
        ],
        cwd,
    )
    if result.returncode != 0:
        return {"branches": [], "hidden_count": 0}
    pr_by_sha = local_pull_request_by_sha(cwd)
    branches: list[dict[str, Any]] = []
    hidden_count = 0
    for line in result.stdout.splitlines():
        name, _, rest = line.partition("\t")
        sha, _, rest = rest.partition("\t")
        updated_ts_text, _, rest = rest.partition("\t")
        updated, _, subject = rest.partition("\t")
        if not name:
            continue
        if len(branches) >= OTHER_BRANCH_LIMIT and name != current_branch:
            hidden_count += 1
            continue
        try:
            updated_ts = int(updated_ts_text)
        except ValueError:
            updated_ts = None
        local_pr = pr_by_sha.get(sha)
        branches.append(
            {
                "name": name,
                "current": name == current_branch,
                "updated": updated or None,
                "updated_ts": updated_ts,
                "head": sha[:12] if sha else None,
                "subject": subject or None,
                "pull_request": local_pr,
                "linear_ids": extract_linear_ids(name, subject),
            }
        )
    return {"branches": branches, "hidden_count": hidden_count}

def local_pull_request_by_sha(cwd: str) -> dict[str, dict[str, Any]]:
    result = git(
        ["for-each-ref", "--format=%(refname:short)\t%(objectname)\t%(subject)", "refs/remotes/origin/pull-request"],
        cwd,
    )
    if result.returncode != 0:
        return {}
    mapping: dict[str, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        ref, _, rest = line.partition("\t")
        sha, _, subject = rest.partition("\t")
        match = re.search(r"(?:^|/)pull-request/(\d+)$", ref)
        if not match or not sha:
            continue
        number = int(match.group(1))
        mapping[sha] = {"number": number, "title": subject.strip() or None}
    return mapping

def regex_int(pattern: str, value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.search(pattern, value)
    if not match:
        return None
    return int(match.group(1))

def pull_request_number_from_subject(subject: str | None) -> int | None:
    return regex_int(r"\(#(\d+)\)\s*$", subject)

def pull_request_number_from_branch(branch: str | None) -> int | None:
    return regex_int(r"^(?:pr|pull-request)[-/](\d+)$", branch)

def parse_github_remote(remote_url: str) -> dict[str, str] | None:
    if not remote_url:
        return None
    if remote_url.startswith("git@github.com:"):
        remote_path = remote_url.split(":", 1)[1]
    else:
        parsed = urlparse(remote_url)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        remote_path = parsed.path.lstrip("/")
    if remote_path.endswith(".git"):
        remote_path = remote_path[:-4]
    parts = [part for part in remote_path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    return {
        "owner": owner,
        "name": name,
        "url": f"https://github.com/{quote(owner)}/{quote(name)}",
    }

class MetadataCache:
    # DOIT.6 #83: bound the cache so dead branch/sha keys (e.g. github-checks:<old_sha> after a push)
    # don't live for the whole process lifetime. Expired entries are swept on write, and a hard cap
    # evicts the soonest-to-expire keys.
    MAX_ENTRIES = 1024

    def __init__(self, ttl_seconds: int = METADATA_CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()
        self.values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        with self.lock:
            item = self.values.get(key)
            if item is None:
                return _CACHE_MISS
            expires_at, value = item
            if expires_at <= time.time():
                self.values.pop(key, None)
                return _CACHE_MISS
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        # DOIT.6 #71: allow a per-entry TTL so a failed/empty fetch can be cached briefly (retry soon)
        # instead of for the full positive TTL.
        with self.lock:
            now = time.time()
            self.values[key] = (now + (self.ttl_seconds if ttl is None else ttl), value)
            # DOIT.6 #83: sweep expired entries on write, then evict the soonest-to-expire keys if still
            # over the cap, so the cache stays bounded and dead keys don't leak.
            if len(self.values) > self.MAX_ENTRIES:
                for dead in [k for k, (expires_at, _) in self.values.items() if expires_at <= now]:
                    self.values.pop(dead, None)
                if len(self.values) > self.MAX_ENTRIES:
                    overflow = len(self.values) - self.MAX_ENTRIES
                    for stale in sorted(self.values, key=lambda k: self.values[k][0])[:overflow]:
                        self.values.pop(stale, None)

def session_project_metadata(info: SessionInfo, cache: MetadataCache, allow_network: bool = True) -> dict[str, Any]:
    git_data = session_git_inventory(info)
    if git_data is None:
        return {"git": None, "pull_request": None, "linear": []}
    enrich_branch_pull_requests(git_data, cache, allow_network=allow_network)

    pull_request = project_pull_request(git_data, cache, allow_network=allow_network)
    linear_ids = extract_linear_ids(
        git_data.get("branch"),
        git_data.get("upstream"),
        git_data.get("head"),
        pull_request.get("title") if pull_request else None,
        pull_request.get("description") if pull_request else None,
        " ".join(pull_request.get("linear_ids", [])) if pull_request else None,
    )
    return {
        "git": git_data,
        "pull_request": pull_request,
        "linear": [linear_issue_metadata(identifier, cache, allow_network=allow_network) for identifier in linear_ids],
    }

def enrich_branch_pull_requests(git_data: dict[str, Any], cache: MetadataCache, allow_network: bool = True) -> None:
    repo = git_data.get("github_repo")
    if not isinstance(repo, dict):
        return
    inventory = git_data.get("other_branches")
    branches = inventory.get("branches") if isinstance(inventory, dict) else None
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        local_pr = branch.get("pull_request")
        number = local_pr.get("number") if isinstance(local_pr, dict) else None
        if not isinstance(number, int):
            continue
        branch["pull_request"] = pull_request_by_number_or_fallback(
            repo,
            number,
            cache,
            allow_network,
            "local-ref",
            local_pr.get("title") if isinstance(local_pr.get("title"), str) else branch.get("subject"),
        )

def session_git_inventory(info: SessionInfo) -> dict[str, Any] | None:
    for cwd in candidate_session_cwds(info):
        git_data = git_inventory(cwd)
        if git_data is not None:
            git_data["cwd"] = cwd
            return git_data
    return None

def candidate_session_cwds(info: SessionInfo) -> list[str]:
    paths: list[str] = []
    default_workdir = session_workdir(info.session)
    if default_workdir.is_dir():
        paths.append(str(default_workdir))
    if info.selected_pane:
        paths.append(info.selected_pane.current_path)
    paths.extend(agent.cwd for agent in info.agents if agent.cwd)
    paths.extend(pane.current_path for pane in info.panes if pane.current_path)
    numbered_workdir = numbered_session_workdir(info.session)
    if numbered_workdir and numbered_workdir.is_dir():
        paths.append(str(numbered_workdir))
    return unique_existing_paths(paths)

def unique_existing_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        try:
            path = str(Path(raw_path).expanduser().resolve())
        except OSError:
            continue
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result

def project_pull_request(git_data: dict[str, Any], cache: MetadataCache, allow_network: bool = True) -> dict[str, Any] | None:
    repo = git_data.get("github_repo")
    if not isinstance(repo, dict):
        return None
    branch = git_data.get("branch")
    if not isinstance(branch, str) or branch in MAIN_BRANCHES or branch == "HEAD":
        return None
    cwd = git_data.get("root") or git_data.get("cwd")
    head_sha = git_data.get("head_sha")
    local_pr = local_pull_request_info(cwd, head_sha) if isinstance(cwd, str) and isinstance(head_sha, str) else None
    if local_pr is not None:
        return pull_request_by_number_or_fallback(
            repo,
            local_pr["number"],
            cache,
            allow_network,
            "local-ref",
            local_pr.get("title"),
        )

    head_subject = str(git_data.get("head") or "")
    subject_pr_number = pull_request_number_from_subject(head_subject)
    if subject_pr_number is not None:
        return pull_request_by_number_or_fallback(
            repo,
            subject_pr_number,
            cache,
            allow_network,
            "head-subject",
            head_subject,
        )

    branch_pr_number = pull_request_number_from_branch(branch)
    if branch_pr_number is not None:
        return pull_request_by_number_or_fallback(
            repo,
            branch_pr_number,
            cache,
            allow_network,
            "branch-name",
            str(git_data.get("head") or branch),
        )
    return github_pull_request_by_branch(repo, branch, cache, allow_network=allow_network)

def local_pull_request_info(cwd: str, head_sha: str) -> dict[str, Any] | None:
    return local_pull_request_by_sha(cwd).get(head_sha)

def pull_request_by_number_or_fallback(
    repo: dict[str, str],
    number: int,
    cache: MetadataCache,
    allow_network: bool,
    source: str,
    title: str | None = None,
) -> dict[str, Any]:
    return github_pull_request_by_number(repo, number, cache, allow_network=allow_network) or fallback_pull_request(
        repo,
        number,
        source,
        title=title,
    )

def fallback_pull_request(repo: dict[str, str], number: int, source: str, title: str | None = None) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": None,
        "merged": False,
        "merged_at": None,
        "draft": False,
        "head_sha": None,
        "url": github_pull_request_url(repo, number),
        "description": title,
        "linear_ids": extract_linear_ids(title),
        "checks": github_checks_unknown(),
        "status_label": "unknown",
        "source": source,
    }

def session_to_json(info: SessionInfo, metadata_cache: MetadataCache, allow_network: bool = True) -> dict[str, Any]:
    return {
        "session": info.session,
        "panes": [asdict(pane) for pane in info.panes],
        "selected_pane": asdict(info.selected_pane) if info.selected_pane else None,
        "agents": [asdict(agent) for agent in info.agents],
        "project": session_project_metadata(info, metadata_cache, allow_network=allow_network),
    }

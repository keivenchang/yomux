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
from .cache import TtlCache
from .common import _CACHE_MISS
from .common import git
from .common import git_ahead_behind_counts
from .github_client import extract_linear_ids
from .github_client import github_checks_unknown
from .github_client import github_pull_request_by_branch
from .github_client import github_pull_request_by_number
from .github_client import github_pull_request_url
from .github_client import pull_request_status_label  # noqa: F401 - re-exported for existing metadata callers
from .github_client import summarize_github_checks  # noqa: F401 - re-exported for existing metadata callers
from .linear_client import linear_issue_metadata
from .settings import settings_payload
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

def git_worktree_identity(cwd: str, toplevel: str) -> dict[str, str] | None:
    """S7: name a LINKED git worktree vs its parent repo, cheaply (one local git call).
    A linked worktree's per-worktree git dir (`.../.git/worktrees/<name>`) differs from the shared
    common dir (`.../.git`); the main worktree's two are identical. Returns the worktree path, its
    name, and the parent (main) repo root — or None when `cwd` is the main worktree / not a worktree."""
    res = git(["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"], cwd)
    if res.returncode != 0:
        return None
    parts = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    if len(parts) < 2:
        return None
    git_dir, common_dir = parts[0], parts[1]
    if not git_dir or not common_dir or git_dir == common_dir:
        return None
    return {"path": toplevel, "parent_root": str(Path(common_dir).parent), "name": Path(git_dir).name}


def linked_worktree_branch_names(cwd: str, toplevel: str) -> set[str]:
    result = git(["worktree", "list", "--porcelain"], cwd)
    if result.returncode != 0:
        return set()
    try:
        root = Path(toplevel).resolve()
    except OSError:
        root = Path(toplevel)
    branches: set[str] = set()
    worktree_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            worktree_path = line.removeprefix("worktree ").strip()
            continue
        if not worktree_path or not line.startswith("branch refs/heads/"):
            continue
        try:
            is_current_root = Path(worktree_path).resolve() == root
        except OSError:
            is_current_root = Path(worktree_path) == root
        if not is_current_root:
            branches.add(line.removeprefix("branch refs/heads/").strip())
    return branches


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
    root_text = root.stdout.strip()
    worktree = git_worktree_identity(cwd, root_text)
    linked_branches = set() if worktree is not None else linked_worktree_branch_names(cwd, root_text)
    return {
        "root": root_text,
        "branch": branch_name,
        "upstream": upstream_name,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "head_sha": head_sha.stdout.strip() if head_sha.returncode == 0 else None,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(status_lines),
        "status": status_lines[:30],
        "github_repo": parse_github_remote(origin_url.stdout.strip()) if origin_url.returncode == 0 else None,
        "other_branches": local_branch_inventory(
            cwd,
            branch_name,
            worktree=worktree,
            linked_worktree_branches=linked_branches,
        ),
        "worktree": worktree,
    }

def git_ahead_behind(cwd: str, upstream: str | None) -> tuple[int | None, int | None]:
    # ahead/behind counting lives in common.git_ahead_behind_counts (one ref order, one parse).
    # HEAD relative to its upstream: ahead = local commits not pushed, behind = upstream commits not pulled.
    if not upstream:
        return None, None
    counts = git_ahead_behind_counts(cwd, upstream, "HEAD")
    return counts if counts is not None else (None, None)

def local_branch_inventory(
    cwd: str,
    current_branch: str | None,
    worktree: dict[str, str] | None = None,
    linked_worktree_branches: set[str] | None = None,
) -> dict[str, Any]:
    result = git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname)\t%(refname:short)\t%(objectname)\t%(committerdate:unix)"
            "\t%(committerdate:relative)\t%(subject)",
            "refs/heads",
            "refs/remotes/origin",
        ],
        cwd,
    )
    if result.returncode != 0:
        return {"branches": [], "hidden_count": 0}
    pr_by_sha = local_pull_request_by_sha(cwd)
    entries: list[dict[str, Any]] = []
    all_local_names: set[str] = set()
    for line in result.stdout.splitlines():
        full_ref, _, rest = line.partition("\t")
        short_ref, _, rest = rest.partition("\t")
        sha, _, rest = rest.partition("\t")
        updated_ts_text, _, rest = rest.partition("\t")
        updated, _, subject = rest.partition("\t")
        source, name = branch_inventory_ref(full_ref, short_ref)
        if not source or not name:
            continue
        if source == "local":
            all_local_names.add(name)
        entries.append(
            {
                "source": source,
                "name": name,
                "sha": sha,
                "updated_ts_text": updated_ts_text,
                "updated": updated,
                "subject": subject,
            }
        )
    branches: list[dict[str, Any]] = []
    hidden_count = 0
    seen_names: set[str] = set()
    local_entries = [entry for entry in entries if entry["source"] == "local"]
    remote_entries = [entry for entry in entries if entry["source"] == "remote"]
    if worktree is not None:
        # Linked worktrees share refs/heads in the common git dir. Let the main checkout own the shared
        # branch inventory; each linked checkout only reports the branch it has checked out.
        local_entries = [entry for entry in local_entries if entry["name"] == current_branch]
        remote_entries = []
    else:
        linked_worktree_branches = linked_worktree_branches or set()
        local_entries = [
            entry
            for entry in local_entries
            if entry["name"] == current_branch or entry["name"] not in linked_worktree_branches
        ]
    local_names = {entry["name"] for entry in local_entries}
    local_namespaces = {
        namespace
        for namespace in (branch_namespace(name) for name in local_names)
        if namespace
    }
    for entry in [*local_entries, *remote_entries]:
        name = entry["name"]
        sha = entry["sha"]
        if name in seen_names:
            continue
        remote_only_pr = entry["source"] == "remote" and name not in all_local_names and sha in pr_by_sha
        if entry["source"] == "remote":
            # Remote refs are huge in shared repos; only promote PR branches from namespaces already present
            # locally so stale or unrelated remote PR refs do not crowd out the checked-out work.
            if not remote_only_pr or branch_namespace(name) not in local_namespaces:
                continue
        if len(branches) >= OTHER_BRANCH_LIMIT and name != current_branch:
            if entry["source"] == "local":
                hidden_count += 1
            continue
        try:
            updated_ts = int(entry["updated_ts_text"])
        except ValueError:
            updated_ts = None
        local_pr = pr_by_sha.get(sha)
        seen_names.add(name)
        branches.append(
            {
                "name": name,
                "current": name == current_branch,
                "remote": entry["source"] == "remote",
                "updated": entry["updated"] or None,
                "updated_ts": updated_ts,
                "head": sha[:12] if sha else None,
                "subject": entry["subject"] or None,
                "pull_request": local_pr,
                "linear_ids": extract_linear_ids(name, entry["subject"]),
            }
        )
    return {"branches": branches, "hidden_count": hidden_count}


def branch_inventory_ref(full_ref: str, short_ref: str) -> tuple[str | None, str | None]:
    if full_ref.startswith("refs/heads/"):
        return "local", full_ref.removeprefix("refs/heads/")
    if full_ref.startswith("refs/remotes/origin/"):
        name = full_ref.removeprefix("refs/remotes/origin/")
        if name == "HEAD" or name.startswith("pull-request/"):
            return None, None
        return "remote", name
    return None, short_ref or None


def branch_namespace(name: str) -> str | None:
    namespace, separator, _ = name.partition("/")
    return namespace if separator and namespace else None


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

# a watched-PR entry is either "owner/repo#N" (or "owner/repo/N") or a full GitHub PR URL.
# An owner/repo segment is a conservative `[A-Za-z0-9._-]+` (GitHub's own allowed set).
_PR_REF_SHORT = re.compile(r"^([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)(?:#|/(?:pull/)?)(\d+)$")

def parse_pull_request_ref(ref: str) -> dict[str, Any] | None:
    """Normalize a watched-PR ref to {owner, name, number, url, ref}.

    Accepts 'owner/repo#N', 'owner/repo/N', and a full 'https://github.com/owner/repo/pull/N' URL.
    Returns None for anything that is not a well-formed GitHub PR reference.
    """
    if not isinstance(ref, str):
        return None
    text = ref.strip()
    if not text:
        return None
    owner = name = None
    number = None
    if "github.com" in text.lower() and "://" in text:
        parsed = urlparse(text)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        # /owner/repo/pull/N (also tolerate /pulls/N)
        if len(parts) >= 4 and parts[2] in {"pull", "pulls"} and parts[3].isdigit():
            owner, name, number = parts[0], parts[1], int(parts[3])
    else:
        match = _PR_REF_SHORT.match(text)
        if match:
            owner, name, number = match.group(1), match.group(2), int(match.group(3))
    if not owner or not name or not isinstance(number, int) or number <= 0:
        return None
    return {
        "owner": owner,
        "name": name,
        "number": number,
        "url": f"https://github.com/{quote(owner)}/{quote(name)}/pull/{number}",
        # Canonical short form used as the per-PR identity (dedupe, notification key, display).
        "ref": f"{owner}/{name}#{number}",
    }

# Cap the watchlist so a long list cannot fan out into an unbounded number of GitHub API calls per poll.
WATCHED_PR_LIMIT = 20

def watched_pr_metadata(
    refs: list[str],
    cache: MetadataCache,
    allow_network: bool = True,
) -> dict[str, Any]:
    """Resolve watched-PR refs to live PR metadata (independent of any local git checkout).

    Returns {"watched_prs": [...], "truncated": <int dropped>, "invalid": [...]}. Each watched_prs
    item is the github_pull_request_by_number dict plus the canonical {ref, url}. Deduped by ref,
    capped at WATCHED_PR_LIMIT.
    """
    seen: set[str] = set()
    parsed_refs: list[dict[str, Any]] = []
    invalid: list[str] = []
    for raw in refs if isinstance(refs, list) else []:
        parsed = parse_pull_request_ref(raw)
        if parsed is None:
            invalid.append(str(raw))
            continue
        if parsed["ref"] in seen:
            continue
        seen.add(parsed["ref"])
        parsed_refs.append(parsed)
    truncated = max(0, len(parsed_refs) - WATCHED_PR_LIMIT)
    results: list[dict[str, Any]] = []
    for parsed in parsed_refs[:WATCHED_PR_LIMIT]:
        repo = {
            "owner": parsed["owner"],
            "name": parsed["name"],
            "url": f"https://github.com/{quote(parsed['owner'])}/{quote(parsed['name'])}",
        }
        pr = github_pull_request_by_number(repo, parsed["number"], cache, allow_network=allow_network)
        if pr is None:
            pr = fallback_pull_request(repo, parsed["number"], "watched")
        pr = {**pr, "ref": parsed["ref"], "url": parsed["url"]}
        results.append(pr)
    return {"watched_prs": results, "truncated": truncated, "invalid": invalid}

class MetadataCache(TtlCache):
    # now a thin TtlCache subclass (which owns the bounded-eviction algorithm this duplicated —
    # #83). Two behaviors preserved via the parent's knobs: get() returns the _CACHE_MISS sentinel
    # (callers distinguish a cached empty value from a miss), and the TTL is measured on the WALL clock
    # (time.time), as the original did, via clock=. Per-entry TTL on set() is inherited.
    def __init__(self, ttl_seconds: int = METADATA_CACHE_TTL_SECONDS):
        super().__init__(ttl_seconds=ttl_seconds, max_entries=1024, clock=time.time)

    def get(self, key: str) -> Any:
        return self.get_or_miss(key)

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
        # C9: all repos this session touches (light local summaries; primary flagged) so the detail bar
        # can show a "+N repos" chip when a session spans more than one repo.
        "repos": session_repo_summaries(info, git_data.get("root")),
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
        if isinstance(number, int):
            branch["pull_request"] = pull_request_by_number_or_fallback(
                repo,
                number,
                cache,
                allow_network,
                "local-ref",
                local_pr.get("title") if isinstance(local_pr.get("title"), str) else branch.get("subject"),
            )
            continue
        # No local (#N) marker — that only appears after a squash-merge, so an OPEN PR on this branch
        # has none and the by-number path above can't find it. Resolve it by HEAD BRANCH instead,
        # mirroring project_pull_request's fallback, so YO!info surfaces open PRs on non-current
        # branches (not just merged ones). Skip the current branch (project_pull_request already
        # resolved its PR) and main/HEAD (no feature PR). github_pull_request_by_branch is cache-keyed,
        # so this is bounded to at most OTHER_BRANCH_LIMIT branch lookups per repo per cache window.
        name = branch.get("name")
        if branch.get("current") or not isinstance(name, str) or name in MAIN_BRANCHES or name == "HEAD":
            continue
        found = github_pull_request_by_branch(repo, name, cache, allow_network=allow_network)
        if found:
            branch["pull_request"] = found

def session_git_inventory(info: SessionInfo) -> dict[str, Any] | None:
    for summary in session_repo_summaries(info, None):
        cwd = summary.get("cwd") or summary.get("root")
        git_data = git_inventory(cwd)
        if git_data is not None:
            git_data["cwd"] = cwd
            git_data["activity_ts"] = summary.get("activity_ts", 0)
            git_data["activity_source"] = summary.get("activity_source", "")
            return git_data
    return None


def status_entry_path(line: str) -> str:
    if len(line) < 4:
        return ""
    path = line[3:].strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1].strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        path = path[1:-1]
    return path


def repo_dirty_activity_ts(root: str, status_lines: list[str]) -> float:
    latest = 0.0
    repo = Path(root)
    for line in status_lines[:200]:
        rel_path = status_entry_path(line)
        if not rel_path:
            continue
        try:
            latest = max(latest, (repo / rel_path).stat().st_mtime)
        except OSError:
            continue
    return latest


def repo_commit_activity_ts(cwd: str) -> float:
    result = git(["log", "-1", "--format=%ct"], cwd)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip() or 0)
    except ValueError:
        return 0.0


def repo_summary(cwd: str | None) -> dict[str, Any] | None:
    # C9: a LIGHT per-repo summary (branch + dirty + ahead/behind) using only cheap LOCAL git — no PR/CI
    # network call and no branch inventory. Used to list every repo a session touches in the detail-bar
    # popover without a per-poll GitHub fetch storm (only the primary repo's PR is fetched eagerly).
    if not cwd:
        return None
    root = git(["rev-parse", "--show-toplevel"], cwd)
    if root.returncode != 0:
        return None
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd)
    status = git(["status", "--short"], cwd)
    upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else None
    ahead, behind = git_ahead_behind(cwd, upstream_name)
    status_lines = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
    dirty_activity_ts = repo_dirty_activity_ts(root.stdout.strip(), status_lines)
    commit_activity_ts = repo_commit_activity_ts(cwd)
    activity_ts = max(dirty_activity_ts, commit_activity_ts)
    return {
        "root": root.stdout.strip(),
        "cwd": str(Path(cwd).expanduser().resolve()),
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(status_lines),
        "activity_ts": activity_ts,
        "activity_source": "dirty" if dirty_activity_ts >= commit_activity_ts and dirty_activity_ts > 0 else ("commit" if commit_activity_ts > 0 else ""),
        "worktree": git_worktree_identity(cwd, root.stdout.strip()),
    }


def session_repo_summaries(info: SessionInfo, primary_root: str | None) -> list[dict[str, Any]]:
    # C9: every git repo the session's panes/agents sit in (cwd -> git root), deduped, with the focused
    # repo flagged. Cheap local git per repo; no transcript scan, no network.
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cwd in candidate_session_cwds(info):
        summary = repo_summary(cwd)
        if not summary or not summary["root"] or summary["root"] in seen:
            continue
        seen.add(summary["root"])
        summary["primary"] = summary["root"] == primary_root
        summaries.append(summary)
    return [
        summary for _index, summary in sorted(
            enumerate(summaries),
            key=lambda item: (-(float(item[1].get("activity_ts") or 0)), item[0]),
        )
    ]

def candidate_session_cwds(info: SessionInfo) -> list[str]:
    # the LIVE pane cwd wins. The session-number default workdir (session "1" -> dynamo1) is
    # only a fallback for a fresh shell sitting in home / a non-repo — it must NOT out-rank a pane that
    # has `cd`'d into a different repo (which previously kept the project pinned to dynamo1 forever).
    paths: list[str] = []
    if info.selected_pane:
        paths.append(info.selected_pane.current_path)        # focused pane's live cwd (follows `cd`)
    paths.extend(agent.cwd for agent in info.agents if agent.cwd)   # agent launch dirs
    paths.extend(pane.current_path for pane in info.panes if pane.current_path)  # other panes
    default_workdir = session_workdir(info.session)          # fallback: session-number default workspace
    if default_workdir.is_dir():
        paths.append(str(default_workdir))
    numbered_workdir = numbered_session_workdir(info.session)   # fallback: numbered workdir
    if numbered_workdir and numbered_workdir.is_dir():
        paths.append(str(numbered_workdir))
    for raw in settings_payload().get("settings", {}).get("file_explorer", {}).get("companion_dirs", []):
        expanded = str(Path(raw).expanduser()) if raw else ""
        if expanded:
            paths.append(expanded)
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
    transcript_mtime = 0.0
    for agent in info.agents:
        if not agent.transcript:
            continue
        try:
            transcript_mtime = max(transcript_mtime, Path(agent.transcript).stat().st_mtime)
        except OSError:
            continue
    return {
        "session": info.session,
        "panes": [asdict(pane) for pane in info.panes],
        "selected_pane": asdict(info.selected_pane) if info.selected_pane else None,
        "agents": [asdict(agent) for agent in info.agents],
        "transcript_mtime": transcript_mtime,
        "project": session_project_metadata(info, metadata_cache, allow_network=allow_network),
    }

from __future__ import annotations

import copy
import re
import threading
import time
from contextlib import contextmanager
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
from .session_files import session_touched_dirs
from .settings import settings_payload
from .workdir import numbered_session_workdir
from .workdir import session_workdir


_METADATA_BUILD_LOCAL = threading.local()


@contextmanager
def metadata_build_cache() -> Any:
    previous = getattr(_METADATA_BUILD_LOCAL, "cache", None)
    if previous is not None:
        yield previous
        return
    cache: dict[str, dict[str, Any]] = {}
    _METADATA_BUILD_LOCAL.cache = cache
    try:
        yield cache
    finally:
        if getattr(_METADATA_BUILD_LOCAL, "cache", None) is cache:
            delattr(_METADATA_BUILD_LOCAL, "cache")


def cached_build_value(bucket: str, key: str, compute: Any, *, copy_value: bool = False) -> Any:
    cache = getattr(_METADATA_BUILD_LOCAL, "cache", None)
    if cache is None:
        return compute()
    values = cache.setdefault(bucket, {})
    if key in values:
        value = values[key]
    else:
        value = compute()
        values[key] = copy.deepcopy(value) if copy_value else value
    return copy.deepcopy(value) if copy_value else value


def resolved_path_text(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except OSError:
        return str(Path(path).expanduser())


def git_root_for_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    cwd_text = resolved_path_text(cwd)

    def compute() -> str | None:
        root = git(["rev-parse", "--show-toplevel"], cwd_text)
        return root.stdout.strip() if root.returncode == 0 else None

    return cached_build_value("git_root_by_cwd", cwd_text, compute)


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


def git_metadata_base(root_text: str) -> dict[str, Any] | None:
    def compute() -> dict[str, Any] | None:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], root_text)
        head_sha = git(["rev-parse", "HEAD"], root_text)
        head = git(["log", "-1", "--pretty=%h %s"], root_text)
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root_text)
        status = git(["status", "--short"], root_text)
        upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else None
        branch_name = branch.stdout.strip() if branch.returncode == 0 else None
        ahead, behind = git_ahead_behind(root_text, upstream_name)
        status_lines = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
        worktree = git_worktree_identity(root_text, root_text)
        linked_branches = set() if worktree is not None else linked_worktree_branch_names(root_text, root_text)
        return {
            "root": root_text,
            "branch": branch_name,
            "upstream": upstream_name,
            "head": head.stdout.strip() if head.returncode == 0 else None,
            "head_sha": head_sha.stdout.strip() if head_sha.returncode == 0 else None,
            "ahead": ahead,
            "behind": behind,
            "status_lines": status_lines,
            "github_repo": github_repo_for_git_remotes(root_text),
            "other_branches": local_branch_inventory(
                root_text,
                branch_name,
                worktree=worktree,
                linked_worktree_branches=linked_branches,
            ),
            "worktree": worktree,
        }

    return cached_build_value("git_metadata_by_root", root_text, compute, copy_value=True)


def git_inventory(cwd: str | None) -> dict[str, Any] | None:
    root_text = git_root_for_cwd(cwd)
    if root_text is None:
        return None
    base = git_metadata_base(root_text)
    if base is None:
        return None
    return {
        "root": base["root"],
        "branch": base["branch"],
        "upstream": base["upstream"],
        "head": base["head"],
        "head_sha": base["head_sha"],
        "ahead": base["ahead"],
        "behind": base["behind"],
        "dirty_count": len(base["status_lines"]),
        "status": base["status_lines"][:30],
        "github_repo": base["github_repo"],
        "other_branches": base["other_branches"],
        "worktree": base["worktree"],
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

def github_repo_for_git_remotes(root_text: str) -> dict[str, str] | None:
    remotes = git(["config", "--get-regexp", r"^remote\..*\.url$"], root_text)
    if remotes.returncode != 0:
        return None
    candidates: list[tuple[int, int, dict[str, str]]] = []
    for index, line in enumerate(remotes.stdout.splitlines()):
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, remote_url = parts
        match = re.match(r"^remote\.(.+)\.url$", key)
        if not match:
            continue
        repo = parse_github_remote(remote_url.strip())
        if repo is None:
            continue
        remote_name = match.group(1)
        priority = {"origin": 0, "upstream": 1}.get(remote_name, 2)
        candidates.append((priority, index, repo))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]

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


def empty_project_metadata(*, loading: bool = False) -> dict[str, Any]:
    return {"git": None, "pull_request": None, "linear": [], "repos": [], "loading": loading}


def session_project_metadata(info: SessionInfo, cache: MetadataCache, allow_network: bool = True) -> dict[str, Any]:
    git_data = session_git_inventory(info)
    if git_data is None:
        return empty_project_metadata()
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
    # C9: a per-repo local summary. It stays network-free, but includes branch inventory so YO!info can
    # show every branch in every repo a session touches; only the primary repo's PRs are enriched eagerly.
    root_text = git_root_for_cwd(cwd)
    if root_text is None:
        return None
    base = git_metadata_base(root_text)
    if base is None:
        return None
    status_lines = base["status_lines"]
    dirty_activity_ts = repo_dirty_activity_ts(root_text, status_lines)
    commit_activity_ts = repo_commit_activity_ts(root_text)
    activity_ts = max(dirty_activity_ts, commit_activity_ts)
    return {
        "root": root_text,
        "cwd": resolved_path_text(cwd),
        "branch": base["branch"],
        "ahead": base["ahead"],
        "behind": base["behind"],
        "dirty_count": len(status_lines),
        "activity_ts": activity_ts,
        "activity_source": "dirty" if dirty_activity_ts >= commit_activity_ts and dirty_activity_ts > 0 else ("commit" if commit_activity_ts > 0 else ""),
        "github_repo": base["github_repo"],
        "other_branches": base["other_branches"],
        "worktree": base["worktree"],
    }


def agent_signature(agent: AgentInfo) -> tuple[Any, ...]:
    transcript_signature: tuple[Any, ...] = ("", 0, 0)
    if agent.transcript:
        try:
            stat = Path(agent.transcript).stat()
            transcript_signature = (str(agent.transcript), stat.st_mtime_ns, stat.st_size)
        except OSError:
            transcript_signature = (str(agent.transcript), 0, 0)
    return (
        agent.kind or "",
        agent.cwd or "",
        agent.pane_target or "",
        agent.session_id or "",
        transcript_signature,
    )


def pane_signature(pane: PaneInfo) -> tuple[Any, ...]:
    return (
        pane.target or "",
        pane.current_path or "",
        pane.window or "",
        pane.pane or "",
        pane.active,
        pane.window_active,
    )


def candidate_cwd_cache_signature(info: SessionInfo) -> str:
    value = (
        info.session,
        pane_signature(info.selected_pane) if info.selected_pane else None,
        tuple(pane_signature(pane) for pane in info.panes),
        tuple(agent_signature(agent) for agent in info.agents),
    )
    return repr(value)

def summary_current_branch_pull_request(summary: dict[str, Any]) -> dict[str, Any] | None:
    branches = summary.get("other_branches", {}).get("branches", [])
    if not isinstance(branches, list):
        return None
    for branch in branches:
        if not isinstance(branch, dict) or branch.get("current") is not True:
            continue
        pull_request = branch.get("pull_request")
        if isinstance(pull_request, dict) and isinstance(pull_request.get("number"), int):
            return pull_request
    return None


def session_repo_summaries(info: SessionInfo, primary_root: str | None) -> list[dict[str, Any]]:
    # C9: every git repo the session's panes/agents sit in (cwd -> git root), deduped, with the focused
    # repo flagged. Edited-file transcript evidence is the strongest signal; activity only ranks repos
    # inside the same signal tier.
    summaries: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    candidates: list[tuple[str, str, int]] = []
    for cwd, priority in candidate_session_cwd_entries(info):
        root = git_root_for_cwd(cwd)
        if not root or root in seen:
            continue
        seen.add(root)
        candidates.append((root, cwd, priority))
    for root, cwd, priority in candidates:
        summary = repo_summary(cwd)
        if not summary or not summary["root"]:
            continue
        summary["primary"] = root == primary_root
        summaries.append((priority, summary))
    return [
        summary for _index, (priority, summary) in sorted(
            enumerate(summaries),
            key=lambda item: (
                item[1][0],
                -int(summary_current_branch_pull_request(item[1][1]) is not None),
                -(float(item[1][1].get("activity_ts") or 0)),
                item[0],
            ),
        )
    ]

def window_metadata(info: SessionInfo, include_git: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    panes_by_window: dict[str, list[Any]] = {}
    for pane in info.panes:
        panes_by_window.setdefault(str(pane.window or ""), []).append(pane)
    for window, panes in sorted(panes_by_window.items(), key=lambda item: (int(item[0]) if str(item[0]).isdigit() else 9999, str(item[0]))):
        chosen = next((pane for pane in panes if pane.active and pane.current_path), None)
        if chosen is None:
            chosen = next((pane for pane in panes if pane.current_path), panes[0] if panes else None)
        if chosen is None:
            continue
        cwd = str(chosen.current_path or "")
        git_data = git_inventory(cwd) if include_git and cwd else None
        if git_data is not None:
            git_data["cwd"] = cwd
        try:
            window_index: int | None = int(window)
        except ValueError:
            window_index = None
        rows.append({
            "window": window,
            "window_index": window_index,
            "window_name": str(chosen.window_name or ""),
            "path": cwd,
            "pane": str(chosen.pane or ""),
            "pane_target": str(chosen.target or ""),
            "git": git_data,
        })
    return rows

def candidate_session_cwds(info: SessionInfo) -> list[str]:
    return [path for path, _priority in candidate_session_cwd_entries(info)]

def candidate_session_cwd_entries(info: SessionInfo) -> list[tuple[str, int]]:
    return cached_build_value("candidate_session_cwd_entries", candidate_cwd_cache_signature(info), lambda: compute_candidate_session_cwd_entries(info), copy_value=True)


def compute_candidate_session_cwd_entries(info: SessionInfo) -> list[tuple[str, int]]:
    # Edited files in transcripts are stronger than cwd because they prove where work actually happened.
    # Live/launch/pane cwd signals share a tier so git activity can still pick the active repo among them.
    paths: list[tuple[str, int]] = []
    paths.extend((path, 0) for path in session_touched_dirs(info))
    if info.selected_pane:
        paths.append((info.selected_pane.current_path, 10))        # focused pane's live cwd (follows `cd`)
    paths.extend((agent.cwd, 10) for agent in info.agents if agent.cwd)   # agent launch dirs
    paths.extend((pane.current_path, 10) for pane in info.panes if pane.current_path)  # other panes
    default_workdir = session_workdir(info.session)          # fallback: session-number default workspace
    if default_workdir.is_dir():
        paths.append((str(default_workdir), 90))
    numbered_workdir = numbered_session_workdir(info.session)   # fallback: numbered workdir
    if numbered_workdir and numbered_workdir.is_dir():
        paths.append((str(numbered_workdir), 90))
    for raw in settings_payload().get("settings", {}).get("file_explorer", {}).get("companion_dirs", []):
        expanded = str(Path(raw).expanduser()) if raw else ""
        if expanded:
            paths.append((expanded, 100))
    return unique_existing_path_entries(paths)

def unique_existing_path_entries(paths: list[tuple[str, int]]) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    seen: set[str] = set()
    for raw_path, priority in paths:
        try:
            path = str(Path(raw_path).expanduser().resolve())
        except OSError:
            continue
        if path in seen:
            continue
        seen.add(path)
        result.append((path, priority))
    return result

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

def session_to_json(info: SessionInfo, metadata_cache: MetadataCache, allow_network: bool = True, include_metadata: bool = True) -> dict[str, Any]:
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
        "window_metadata": window_metadata(info, include_git=include_metadata),
        "transcript_mtime": transcript_mtime,
        "project": session_project_metadata(info, metadata_cache, allow_network=allow_network) if include_metadata else empty_project_metadata(loading=True),
        "metadata_loading": not include_metadata,
    }

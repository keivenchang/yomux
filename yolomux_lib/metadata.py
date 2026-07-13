from __future__ import annotations

import copy
import hashlib
import os
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
from .common import TmuxPaneInfo
from .common import SessionInfo
from .cache import TtlCache
from .common import _CACHE_MISS
from .common import git
from .common import git_ahead_behind_counts
from .filesystem.search import SEARCH_SKIP_DIRS
from .github_client import extract_linear_ids
from .github_client import github_checks_unknown
from .github_client import github_pull_request_by_branch
from .github_client import github_pull_request_branch_lookup_state
from .github_client import github_pull_requests_by_branch
from .github_client import github_pull_request_by_number
from .github_client import github_pull_request_url
from .github_client import pull_request_status_label  # noqa: F401 - re-exported for existing metadata callers
from .github_client import summarize_github_checks  # noqa: F401 - re-exported for existing metadata callers
from .linear_client import linear_issue_metadata
from .locales import message_fields
from .session_files import scan_agent_changes
from .session_files import session_touched_dirs
from .settings import settings_payload
from .workdir import numbered_session_workdir
from .workdir import session_workdir


_METADATA_BUILD_LOCAL = threading.local()
# Indexed directories can be very large. Discovery is intentionally slower
# than per-repository Git metadata, so share one bounded scan across every
# request/thread rather than letting concurrent metadata readers each os.walk
# the same tree. Changing the configured directory list changes the cache key.
INDEXED_REPO_ROOTS_CACHE_SECONDS = 30.0
_INDEXED_REPO_ROOTS_CACHE_LOCK = threading.Lock()
_INDEXED_REPO_ROOTS_CACHE: dict[tuple[str, ...], tuple[float, list[str]]] = {}
# A full metadata snapshot launches several Git commands.  Keep that work behind
# one process-wide, source-keyed owner so unrelated API/background readers do not
# rebuild the same unchanged repository independently.
_GIT_METADATA_CACHE_LOCK = threading.Lock()
GIT_METADATA_BURST_COALESCE_SECONDS = 0.25
_GIT_METADATA_CACHE: dict[tuple[str, int | None], tuple[tuple[Any, ...], dict[str, Any] | None, float]] = {}
_GIT_METADATA_INFLIGHT: dict[tuple[str, int | None], threading.Event] = {}
# Worktree branch history is deliberately small, process-local metadata. Git describes the current
# checkout, while this preserves a branch a visible worktree used earlier in the same server lifetime
# (including a branch later deleted or renamed) without inventing a second branch inventory owner.
_WORKTREE_BRANCH_HISTORY_LOCK = threading.Lock()
_WORKTREE_BRANCH_HISTORY: dict[str, dict[str, dict[str, Any]]] = {}


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
        candidate = Path(cwd_text)
        if candidate.is_file():
            candidate = candidate.parent
        for parent in (candidate, *candidate.parents):
            if (parent / ".git").exists():
                return str(parent)
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

def pane_inventory(pane: TmuxPaneInfo | None, focus_root: str | None) -> dict[str, Any] | None:
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

def git_worktree_paths(cwd: str) -> tuple[str, str] | None:
    result = git(["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"], cwd)
    if result.returncode != 0:
        return None
    parts = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def git_worktree_identity(cwd: str, toplevel: str) -> dict[str, str] | None:
    """S7: name a LINKED git worktree vs its parent repo, cheaply (one local git call).
    A linked worktree's per-worktree git dir (`.../.git/worktrees/<name>`) differs from the shared
    common dir (`.../.git`); the main worktree's two are identical. Returns the worktree path, its
    name, and the parent (main) repo root — or None when `cwd` is the main worktree / not a worktree."""
    paths = git_worktree_paths(cwd)
    if paths is None:
        return None
    git_dir, common_dir = paths
    if not git_dir or not common_dir or git_dir == common_dir:
        return None
    return {"path": toplevel, "parent_root": str(Path(common_dir).parent), "name": Path(git_dir).name}


def git_local_repository_identity(cwd: str, toplevel: str) -> dict[str, Any] | None:
    """Return canonical local-repository/worktree identities for one checked-out path.

    `git-common-dir` identifies one local repository's shared refs and object database. A linked
    worktree has its own git dir but the same common dir; independent clones remain separate even
    when their remotes point to the same hosted repository.
    """
    paths = git_worktree_paths(cwd)
    if paths is None:
        return None
    git_dir, common_dir = paths
    root = resolved_path_text(toplevel)
    common = resolved_path_text(common_dir)
    worktree_git_dir = resolved_path_text(git_dir)
    return {
        "id": f"local-git:{common}",
        "common_git_dir": common,
        "worktree_id": f"git-worktree:{root}",
        "worktree_git_dir": worktree_git_dir,
        "kind": "primary" if worktree_git_dir == common else "linked",
    }


def git_metadata_base(
    root_text: str,
    branch_limit: int | None = OTHER_BRANCH_LIMIT,
) -> dict[str, Any] | None:
    cache_key = (root_text, branch_limit)
    with _GIT_METADATA_CACHE_LOCK:
        cached = _GIT_METADATA_CACHE.get(cache_key)
        if cached is not None and time.monotonic() - cached[2] <= GIT_METADATA_BURST_COALESCE_SECONDS:
            return copy.deepcopy(cached[1])
        inflight = _GIT_METADATA_INFLIGHT.get(cache_key)
        if inflight is None:
            inflight = threading.Event()
            _GIT_METADATA_INFLIGHT[cache_key] = inflight
            owner = True
        else:
            owner = False
    if not owner:
        inflight.wait()
        with _GIT_METADATA_CACHE_LOCK:
            cached = _GIT_METADATA_CACHE.get(cache_key)
        return copy.deepcopy(cached[1]) if cached is not None else None

    def compute(status: Any) -> dict[str, Any] | None:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], root_text)
        head_sha = git(["rev-parse", "HEAD"], root_text)
        # An unborn repository has a symbolic initial branch (normally `main`) but no HEAD commit.
        # `rev-parse --abbrev-ref HEAD` reports `HEAD` in that state, which would make the graph
        # invent neither a useful current branch nor an honest null SHA. Read the symbolic ref only
        # for that state; detached HEAD intentionally remains branchless.
        branch_name = branch.stdout.strip() if branch.returncode == 0 else None
        if head_sha.returncode != 0 and (not branch_name or branch_name == "HEAD"):
            symbolic_head = git(["symbolic-ref", "--quiet", "--short", "HEAD"], root_text)
            if symbolic_head.returncode == 0 and symbolic_head.stdout.strip():
                branch_name = symbolic_head.stdout.strip()
        head = git(["log", "-1", "--pretty=%h %s"], root_text)
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root_text)
        upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else None
        ahead, behind = git_ahead_behind(root_text, upstream_name)
        status_lines = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
        worktree = git_worktree_identity(root_text, root_text)
        local_repository = git_local_repository_identity(root_text, root_text)
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
                branch_limit=branch_limit,
            ),
            "worktree": worktree,
            "local_repository": local_repository,
        }

    try:
        status = git(["status", "--short", "--untracked-files=all"], root_text, timeout=10.0)
        status_text = status.stdout if status.returncode == 0 else f"error:{status.returncode}:{status.stderr}"
        source_signature = (
            hashlib.sha256(status_text.encode("utf-8", errors="replace")).hexdigest(),
            git_control_files_signature(root_text),
        )
        with _GIT_METADATA_CACHE_LOCK:
            cached = _GIT_METADATA_CACHE.get(cache_key)
        if cached is not None and cached[0] == source_signature:
            value = cached[1]
        else:
            value = compute(status)
        with _GIT_METADATA_CACHE_LOCK:
            _GIT_METADATA_CACHE[cache_key] = (source_signature, copy.deepcopy(value), time.monotonic())
            if len(_GIT_METADATA_CACHE) > 256:
                _GIT_METADATA_CACHE.pop(next(iter(_GIT_METADATA_CACHE)))
        return copy.deepcopy(value)
    finally:
        with _GIT_METADATA_CACHE_LOCK:
            _GIT_METADATA_INFLIGHT.pop(cache_key, None)
            inflight.set()


def git_control_files_signature(root_text: str) -> tuple[Any, ...]:
    """Cheaply identify ref/index/config changes without launching Git."""
    marker = Path(root_text) / ".git"
    git_dir = marker
    if marker.is_file():
        try:
            first_line = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        if first_line.lower().startswith("gitdir:"):
            git_dir = Path(first_line.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = marker.parent / git_dir
    common_dir = git_dir
    try:
        common_text = (git_dir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        common_text = ""
    if common_text:
        common_dir = Path(common_text)
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir

    paths = [git_dir / "HEAD", git_dir / "index", common_dir / "packed-refs", common_dir / "config"]
    refs_dir = common_dir / "refs"
    if refs_dir.is_dir():
        for current, dirs, files in os.walk(refs_dir, topdown=True, followlinks=False):
            dirs[:] = sorted(dirs)
            paths.extend(Path(current) / name for name in sorted(files))
    rows: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
            rows.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            rows.append((str(path), 0, 0))
    return tuple(rows)


def git_inventory(cwd: str | None, branch_limit: int | None = OTHER_BRANCH_LIMIT) -> dict[str, Any] | None:
    root_text = git_root_for_cwd(cwd)
    if root_text is None:
        return None
    base = git_metadata_base(root_text, branch_limit=branch_limit)
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
        "local_repository": base["local_repository"],
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
    branch_limit: int | None = OTHER_BRANCH_LIMIT,
) -> dict[str, Any]:
    result = git(
        [
            "branch",
            "--format=%(refname)\t%(refname:short)\t%(objectname)\t%(committerdate:unix)"
            "\t%(committerdate:relative)\t%(subject)",
        ],
        cwd,
    )
    if result.returncode != 0:
        return {"branches": [], "hidden_count": 0}
    pr_by_sha = local_pull_request_by_sha(cwd)
    entries: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        full_ref, _, rest = line.partition("\t")
        short_ref, _, rest = rest.partition("\t")
        sha, _, rest = rest.partition("\t")
        updated_ts_text, _, rest = rest.partition("\t")
        updated, _, subject = rest.partition("\t")
        source, name = branch_inventory_ref(full_ref, short_ref)
        if not source or not name:
            continue
        entries.append(
            {
                "source": source,
                "name": name,
                "sha": sha,
                # This is the branch HEAD commit's committer date, never a working-tree file mtime.
                "updated_ts_text": updated_ts_text,
                "updated": updated,
                "subject": subject,
            }
        )
    branches: list[dict[str, Any]] = []
    hidden_count = 0
    seen_names: set[str] = set()
    # Local branches belong to the shared local Git repository, not to a single primary or linked
    # worktree. Every worktree receives the complete inventory; its own checked-out branch remains
    # represented by `current`. The legacy parameters remain temporarily for API compatibility.
    del worktree, linked_worktree_branches
    local_entries = [entry for entry in entries if entry["source"] == "local"]
    for entry in local_entries:
        name = entry["name"]
        sha = entry["sha"]
        if name in seen_names:
            continue
        if branch_limit is not None and len(branches) >= branch_limit and name != current_branch:
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
                # The abbreviated head is a legacy display value. Normalized history needs the
                # immutable full object ID after a branch ref is renamed or deleted.
                "head_sha": sha or None,
                "subject": entry["subject"] or None,
                "pull_request": local_pr,
                "linear_ids": extract_linear_ids(name, entry["subject"]),
            }
        )
    # A repository before its first commit has no refs/heads entry to enumerate. It still has a
    # meaningful configured branch, and the normalized graph needs to show it with no head SHA
    # instead of pretending it is detached or has no current checkout.
    if current_branch and current_branch != "HEAD" and current_branch not in seen_names:
        branches.append(
            {
                "name": current_branch,
                "current": True,
                "remote": False,
                "updated": None,
                "updated_ts": None,
                "head": None,
                "head_sha": None,
                "subject": None,
                "pull_request": None,
                "linear_ids": extract_linear_ids(current_branch),
                "unborn": True,
            }
        )
    return {"branches": branches, "hidden_count": hidden_count}


def branch_inventory_ref(full_ref: str, short_ref: str) -> tuple[str | None, str | None]:
    if full_ref.startswith("refs/heads/"):
        return "local", full_ref.removeprefix("refs/heads/")
    return None, short_ref or None


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


WORK_GRAPH_VERSION = 1


def tmux_pane_graph_id(pane: TmuxPaneInfo) -> str:
    return f"tmux-pane:{pane.session}:{pane.window}:{pane.pane}"


def tmux_window_graph_id(session: str, window: str) -> str:
    return f"tmux-window:{session}:{window}"


def runtime_actor_graph_id(agent: AgentInfo | None, pane: TmuxPaneInfo, ordinal: int = 0) -> str:
    if agent is None:
        return f"runtime-actor:{tmux_pane_graph_id(pane)}:shell:{pane.pid}"
    # A PID alone is not durable: constrain it by the tmux pane plus transcript/session identity, so a
    # reused PID cannot silently become an old actor during a metadata refresh.
    signature = "|".join(
        [
            agent.kind or "process",
            str(agent.pid),
            agent.session_id or "",
            agent.transcript or "",
            agent.cwd or "",
            str(ordinal),
        ]
    )
    return f"runtime-actor:{tmux_pane_graph_id(pane)}:{quote(signature, safe='')}"


def pane_for_agent(info: SessionInfo, agent: AgentInfo) -> TmuxPaneInfo | None:
    expected_target = agent.pane_target
    for pane in info.panes:
        if expected_target in {pane.target, pane.pane_id, f"{pane.session}:{pane.window}.{pane.pane}"}:
            return pane
    return None


def observation_path_text(raw_path: str) -> tuple[str, bool]:
    path = Path(raw_path).expanduser()
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    return str(resolved), resolved.exists()


def observation_git_cwd(path_text: str, exists: bool) -> str | None:
    path = Path(path_text)
    if exists and path.is_file():
        return str(path.parent)
    if exists and path.is_dir():
        return str(path)
    # Historical edits can name a deleted file below a deleted directory. Walk upward to the nearest
    # surviving parent so the evidence remains associated with the repository that contained it.
    for parent in (path.parent, *path.parents):
        if parent.is_dir():
            return str(parent)
    return None


def resolve_path_observation(path_text: str, git_inventory_by_root: dict[str, dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """Resolve one observed path once; callers use its canonical worktree/local-repo identity."""
    path, exists = observation_path_text(path_text)
    git_cwd = observation_git_cwd(path, exists)
    root = git_root_for_cwd(git_cwd) if git_cwd else None
    if root is None:
        return {"path": path, "exists": exists, "git_cwd": git_cwd, "git_root": None}
    cache = git_inventory_by_root if git_inventory_by_root is not None else {}
    if root not in cache:
        cache[root] = git_inventory(git_cwd, branch_limit=None)
    git_data = cache[root]
    local_identity = git_data.get("local_repository") if isinstance(git_data, dict) else None
    return {
        "path": path,
        "exists": exists,
        "git_cwd": git_cwd,
        "git_root": root,
        "git_data": git_data,
        "git_worktree_id": local_identity.get("worktree_id") if isinstance(local_identity, dict) else None,
        "local_repository_id": local_identity.get("id") if isinstance(local_identity, dict) else None,
    }


def work_graph_path_observations(info: SessionInfo) -> list[dict[str, Any]]:
    now = time.time()
    observations: list[dict[str, Any]] = []
    counter = 0

    def add(
        raw_path: str | None,
        *,
        source: str,
        priority: int,
        tmux_pane_id: str | None = None,
        runtime_actor_id: str | None = None,
    ) -> None:
        nonlocal counter
        if not raw_path:
            return
        path, exists = observation_path_text(raw_path)
        counter += 1
        observations.append(
            {
                "id": f"path-observation:{info.session}:{counter}",
                "path": path,
                "path_snapshot": str(raw_path),
                "exists": exists,
                "source": source,
                "priority": priority,
                "first_observed_at": now,
                "last_observed_at": now,
                "tmux_pane_id": tmux_pane_id,
                "runtime_actor_id": runtime_actor_id,
                "git_worktree_id": None,
            }
        )

    if info.selected_pane:
        add(
            info.selected_pane.current_path,
            source="selected-pane-cwd",
            # Focus tells us where the user is looking, not where work happened. Keep it in the
            # same live-cwd tier as every other tmux pane so a newer dirty repo or an open PR can
            # win inside that tier. Transcript edits remain the stronger work evidence below.
            priority=10,
            tmux_pane_id=tmux_pane_graph_id(info.selected_pane),
        )
    actors_by_pane: dict[str, list[tuple[AgentInfo, str]]] = {}
    for ordinal, agent in enumerate(info.agents):
        pane = pane_for_agent(info, agent)
        if pane is None:
            continue
        actor_id = runtime_actor_graph_id(agent, pane, ordinal)
        actors_by_pane.setdefault(tmux_pane_graph_id(pane), []).append((agent, actor_id))
        add(agent.cwd, source="actor-cwd", priority=10, tmux_pane_id=tmux_pane_graph_id(pane), runtime_actor_id=actor_id)
        for changed_path in scan_agent_changes(agent):
            # An explicit transcript edit is stronger evidence than a cwd. This is the canonical
            # replacement for the former singular-project ranking; never let a focused stale cwd
            # hide the repository where the actor actually edited a file.
            add(changed_path, source="edit", priority=0, tmux_pane_id=tmux_pane_graph_id(pane), runtime_actor_id=actor_id)
    for pane in info.panes:
        pane_id = tmux_pane_graph_id(pane)
        add(pane.current_path, source="pane-cwd", priority=10, tmux_pane_id=pane_id)
    # Candidate discovery includes transcript-derived paths and configured session fallbacks that do
    # not necessarily belong to a currently listed tmux pane. Keep each as a first-class graph edge
    # instead of making a separate legacy summary scan reach outside the graph.
    for candidate_path, candidate_priority in candidate_session_cwd_entries(info):
        add(candidate_path, source="candidate-cwd", priority=candidate_priority)
    if not observations:
        default_workdir = session_workdir(info.session)
        if default_workdir.is_dir():
            add(str(default_workdir), source="session-workdir", priority=90)
        numbered_workdir = numbered_session_workdir(info.session)
        if numbered_workdir and numbered_workdir.is_dir():
            add(str(numbered_workdir), source="numbered-session-workdir", priority=90)
    return observations


def hosted_repository_graph_id(repo: dict[str, str]) -> str:
    return f"hosted-repository:github.com/{repo['owner']}/{repo['name']}"


def local_branch_graph_id(local_repository_id: str, branch_name: str) -> str:
    return f"local-branch:{local_repository_id}:{quote(branch_name, safe='')}"


def pull_request_graph_id(hosted_repository_id: str, number: int) -> str:
    return f"pull-request:{hosted_repository_id}:{number}"


def add_branch_activity(
    graph: dict[str, Any],
    *,
    worktree_id: str,
    branch_id: str | None,
    branch_name_snapshot: str,
    observed_head_sha: str | None,
    observation: dict[str, Any],
    current: bool,
) -> None:
    activity_key = branch_id or quote(branch_name_snapshot, safe="")
    activity_id = f"worktree-branch-activity:{worktree_id}:{activity_key}"
    activity = graph["worktree_branch_activity"].setdefault(
        activity_id,
        {
            "id": activity_id,
            "git_worktree_id": worktree_id,
            "local_branch_id": branch_id,
            "branch_name_snapshot": branch_name_snapshot,
            "observed_head_sha": observed_head_sha,
            "current": current,
            "first_observed_at": observation["first_observed_at"],
            "last_observed_at": observation["last_observed_at"],
            "path_observation_ids": [],
        },
    )
    activity["current"] = activity["current"] or current
    if observed_head_sha:
        activity["observed_head_sha"] = observed_head_sha
    activity["first_observed_at"] = min(activity["first_observed_at"], observation["first_observed_at"])
    activity["last_observed_at"] = max(activity["last_observed_at"], observation["last_observed_at"])
    if observation["id"] not in activity["path_observation_ids"]:
        activity["path_observation_ids"].append(observation["id"])
    worktree = graph["git_worktrees"][worktree_id]
    if activity_id not in worktree["branch_activity_ids"]:
        worktree["branch_activity_ids"].append(activity_id)


def record_worktree_branch_history(
    worktree_id: str,
    branch: dict[str, Any],
    observed_at: float,
) -> dict[str, dict[str, Any]]:
    name = branch.get("name")
    if not isinstance(name, str) or not name:
        return {}
    snapshot = {
        "name": name,
        "head": branch.get("head"),
        "head_sha": branch.get("head_sha"),
        "subject": branch.get("subject"),
        "updated": branch.get("updated"),
        "updated_ts": branch.get("updated_ts"),
        "first_observed_at": observed_at,
        "last_observed_at": observed_at,
    }
    with _WORKTREE_BRANCH_HISTORY_LOCK:
        histories = _WORKTREE_BRANCH_HISTORY.setdefault(worktree_id, {})
        existing = histories.get(name)
        if existing:
            snapshot["first_observed_at"] = min(float(existing.get("first_observed_at") or observed_at), observed_at)
        histories[name] = snapshot
        return copy.deepcopy(histories)


def associate_branch_pull_request(
    graph: dict[str, Any],
    *,
    hosted_repository_id: str,
    local_branch_id: str | None,
    pull_request: dict[str, Any],
    cache: MetadataCache,
    allow_network: bool,
) -> None:
    number = pull_request.get("number")
    if not isinstance(number, int):
        return
    pr_id = pull_request_graph_id(hosted_repository_id, number)
    canonical_pr = graph["pull_requests"].setdefault(
        pr_id,
        {"id": pr_id, "hosted_repository_id": hosted_repository_id, **copy.deepcopy(pull_request), "local_branch_ids": [], "linear_issue_ids": []},
    )
    head_repository = pull_request.get("head_repository")
    if isinstance(head_repository, dict) and isinstance(head_repository.get("full_name"), str):
        owner, separator, name = head_repository["full_name"].partition("/")
        if separator and owner and name:
            head_hosted_repository = {"owner": owner, "name": name, "url": str(head_repository.get("url") or f"https://github.com/{owner}/{name}")}
            head_hosted_repository_id = hosted_repository_graph_id(head_hosted_repository)
            graph["hosted_repositories"].setdefault(
                head_hosted_repository_id,
                {"id": head_hosted_repository_id, "provider": "github", **head_hosted_repository, "local_repository_ids": [], "pull_request_ids": []},
            )
            canonical_pr["head_hosted_repository_id"] = head_hosted_repository_id
    hosted_repository = graph["hosted_repositories"][hosted_repository_id]
    if pr_id not in hosted_repository["pull_request_ids"]:
        hosted_repository["pull_request_ids"].append(pr_id)
    branch = graph["local_branches"].get(local_branch_id) if isinstance(local_branch_id, str) else None
    if branch is not None:
        if pr_id not in branch["pull_request_ids"]:
            branch["pull_request_ids"].append(pr_id)
        if local_branch_id not in canonical_pr["local_branch_ids"]:
            canonical_pr["local_branch_ids"].append(local_branch_id)
    for linear_id in extract_linear_ids(
        branch.get("name") if branch is not None else None,
        branch.get("subject") if branch is not None else None,
        canonical_pr.get("title"),
        canonical_pr.get("description"),
        " ".join(str(item) for item in canonical_pr.get("linear_ids", [])),
    ):
        ensure_graph_linear_issue(graph, linear_id, cache, allow_network=allow_network)
        if branch is not None and linear_id not in branch["linear_issue_ids"]:
            branch["linear_issue_ids"].append(linear_id)
        if linear_id not in canonical_pr["linear_issue_ids"]:
            canonical_pr["linear_issue_ids"].append(linear_id)


def associate_branch_linear_issues(
    graph: dict[str, Any],
    *,
    local_branch_id: str,
    identifiers: list[str],
    cache: MetadataCache,
    allow_network: bool,
) -> None:
    """Attach one canonical Linear record to a branch without copying it through path projections."""
    branch = graph["local_branches"].get(local_branch_id)
    if branch is None:
        return
    for identifier in identifiers:
        if not isinstance(identifier, str) or not identifier:
            continue
        ensure_graph_linear_issue(graph, identifier, cache, allow_network=allow_network)
        if identifier not in branch["linear_issue_ids"]:
            branch["linear_issue_ids"].append(identifier)


def ensure_graph_linear_issue(
    graph: dict[str, Any],
    identifier: str,
    cache: MetadataCache,
    *,
    allow_network: bool,
) -> None:
    if identifier not in graph["linear_issues"]:
        graph["linear_issues"][identifier] = cached_linear_issue_metadata(identifier, cache, allow_network=allow_network)


def branch_pr_lookup_state(
    branch: dict[str, Any],
    *,
    has_hosted_repository: bool,
    allow_network: bool,
) -> str:
    state = branch.get("pull_request_lookup_state")
    if state in {"loading", "ready", "none", "stale", "error", "not-requested"}:
        return state
    if not has_hosted_repository:
        return "not-requested"
    if branch.get("pull_request"):
        return "ready"
    name = branch.get("name")
    if not isinstance(name, str) or name in MAIN_BRANCHES or name == "HEAD":
        return "none"
    return "none" if allow_network else "not-requested"


def distinct_pull_requests(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve open-first API order while making branch-to-PR associations canonical by number."""
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for value in values:
        number = value.get("number")
        if not isinstance(number, int) or number in seen:
            continue
        seen.add(number)
        result.append(value)
    return result


def branch_pull_requests(
    repo: dict[str, str],
    branch_name: str,
    cache: MetadataCache,
    *,
    allow_network: bool,
    known: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Resolve the full PR set and preserve old single-PR callers as a cache-safe compatibility seam."""
    values = distinct_pull_requests(list(known or []))
    found = github_pull_requests_by_branch(repo, branch_name, cache, allow_network=allow_network)
    state = github_pull_request_branch_lookup_state(repo, branch_name, cache)
    if found:
        return distinct_pull_requests([*values, *found]), "ready"
    if values and not allow_network:
        return values, "ready"
    # The old helper delegates to the plural cache in production, so this does not add a second HTTP
    # request. It does keep the established seam for callers/tests that supply a local direct lookup.
    legacy = github_pull_request_by_branch(repo, branch_name, cache, allow_network=allow_network)
    if isinstance(legacy, dict):
        return distinct_pull_requests([*values, legacy]), "ready"
    if state == "error":
        # Existing local evidence remains useful, but must be labelled stale rather than fresh.
        return values, "stale" if values else "error"
    if not allow_network:
        return values, "ready" if values else "not-requested"
    return values, "ready" if values else "none"


def empty_work_graph(*, loading: bool = False) -> dict[str, Any]:
    return {
        "version": WORK_GRAPH_VERSION,
        "loading": loading,
        "generation": 0,
        "tmux_sessions": {},
        "tmux_windows": {},
        "tmux_panes": {},
        "runtime_actors": {},
        "path_observations": {},
        "git_worktrees": {},
        "local_repositories": {},
        "hosted_repositories": {},
        "local_branches": {},
        "pull_requests": {},
        "linear_issues": {},
        "worktree_branch_activity": {},
    }


def session_work_graph(info: SessionInfo, cache: MetadataCache, allow_network: bool = True) -> dict[str, Any]:
    """Build the one normalized metadata graph used by both new and legacy API projections."""
    graph = empty_work_graph()
    # Generation is monotonic within this process. The API consumer can reject a slower, older
    # metadata refresh without relying on wall-clock ordering across machines or clock changes.
    previous_generation = getattr(_METADATA_BUILD_LOCAL, "work_graph_generation", 0)
    generation = max(time.time_ns(), previous_generation + 1)
    _METADATA_BUILD_LOCAL.work_graph_generation = generation
    graph["generation"] = generation
    git_inventory_by_root: dict[str, dict[str, Any] | None] = {}
    session_id = f"tmux-session:{info.session}"
    graph["tmux_sessions"][session_id] = {
        "id": session_id,
        "name": info.session,
        "tmux_window_ids": [],
        "tmux_pane_ids": [],
        "runtime_actor_ids": [],
        "path_observation_ids": [],
    }
    panes_by_id: dict[str, TmuxPaneInfo] = {}
    for pane in info.panes:
        pane_id = tmux_pane_graph_id(pane)
        window_id = tmux_window_graph_id(info.session, pane.window)
        panes_by_id[pane_id] = pane
        graph["tmux_windows"].setdefault(
            window_id,
            {"id": window_id, "tmux_session_id": session_id, "index": pane.window, "name": pane.window_name, "tmux_pane_ids": []},
        )
        graph["tmux_windows"][window_id]["tmux_pane_ids"].append(pane_id)
        graph["tmux_panes"][pane_id] = {
            "id": pane_id,
            "tmux_window_id": window_id,
            "target": pane.target,
            "pane_id": pane.pane_id,
            "index": pane.pane,
            "current_path": pane.current_path,
            "command": pane.command,
            "active": pane.active,
            "window_active": pane.window_active,
            "runtime_actor_ids": [],
            "path_observation_ids": [],
        }
        graph["tmux_sessions"][session_id]["tmux_pane_ids"].append(pane_id)
        if window_id not in graph["tmux_sessions"][session_id]["tmux_window_ids"]:
            graph["tmux_sessions"][session_id]["tmux_window_ids"].append(window_id)

    actor_pane_ids: set[str] = set()
    for ordinal, agent in enumerate(info.agents):
        pane = pane_for_agent(info, agent)
        if pane is None:
            continue
        pane_id = tmux_pane_graph_id(pane)
        actor_id = runtime_actor_graph_id(agent, pane, ordinal)
        actor_pane_ids.add(pane_id)
        graph["runtime_actors"][actor_id] = {
            "id": actor_id,
            "tmux_pane_id": pane_id,
            "kind": agent.kind,
            "pid": agent.pid,
            "command": agent.command,
            "cwd": agent.cwd,
            "status": agent.status,
            "agent_session_id": agent.session_id,
            "transcript": agent.transcript,
            "error": agent.error,
            "model": agent.model,
            "path_observation_ids": [],
        }
        graph["tmux_panes"][pane_id]["runtime_actor_ids"].append(actor_id)
        graph["tmux_sessions"][session_id]["runtime_actor_ids"].append(actor_id)
    for pane_id, pane in panes_by_id.items():
        if pane_id in actor_pane_ids:
            continue
        actor_id = runtime_actor_graph_id(None, pane)
        graph["runtime_actors"][actor_id] = {
            "id": actor_id,
            "tmux_pane_id": pane_id,
            "kind": "shell",
            "pid": pane.pid,
            "command": pane.command,
            "cwd": pane.current_path,
            "status": None,
            "agent_session_id": None,
            "transcript": None,
            "error": None,
            "model": None,
            "path_observation_ids": [],
        }
        graph["tmux_panes"][pane_id]["runtime_actor_ids"].append(actor_id)
        graph["tmux_sessions"][session_id]["runtime_actor_ids"].append(actor_id)

    for observation in work_graph_path_observations(info):
        observation_id = observation["id"]
        graph["path_observations"][observation_id] = observation
        graph["tmux_sessions"][session_id]["path_observation_ids"].append(observation_id)
        pane_id = observation.get("tmux_pane_id")
        actor_id = observation.get("runtime_actor_id")
        if isinstance(pane_id, str) and pane_id in graph["tmux_panes"]:
            graph["tmux_panes"][pane_id]["path_observation_ids"].append(observation_id)
        if isinstance(actor_id, str) and actor_id in graph["runtime_actors"]:
            graph["runtime_actors"][actor_id]["path_observation_ids"].append(observation_id)
        resolved_observation = resolve_path_observation(observation["path"], git_inventory_by_root)
        root = resolved_observation["git_root"]
        if root is None:
            continue
        git_data = git_inventory_by_root[root]
        if not git_data:
            continue
        local_identity = git_data.get("local_repository")
        if not isinstance(local_identity, dict):
            continue
        worktree_id = local_identity["worktree_id"]
        local_repository_id = local_identity["id"]
        observation["git_worktree_id"] = worktree_id
        worktree = graph["git_worktrees"].setdefault(
            worktree_id,
            {
                "id": worktree_id,
                "root": git_data["root"],
                "git_dir": local_identity["worktree_git_dir"],
                "kind": local_identity["kind"],
                "local_repository_id": local_repository_id,
                "hosted_repository_id": None,
                "current_branch_id": None,
                "branch_activity_ids": [],
                "path_observation_ids": [],
                # Ranking data belongs to the canonical worktree. Activity consumers can choose a
                # compact summary without reviving an independent session.project owner.
                "activity_priority": 999,
                "activity_ts": 0.0,
                "activity_source": "",
                "has_current_pull_request": False,
                "git": {},
            },
        )
        worktree["path_observation_ids"].append(observation_id)
        hosted_id: str | None = None
        github_repo = git_data.get("github_repo")
        if isinstance(github_repo, dict):
            hosted_id = hosted_repository_graph_id(github_repo)
            graph["hosted_repositories"].setdefault(
                hosted_id,
                {"id": hosted_id, "provider": "github", **github_repo, "local_repository_ids": [], "pull_request_ids": []},
            )
            if local_repository_id not in graph["hosted_repositories"][hosted_id]["local_repository_ids"]:
                graph["hosted_repositories"][hosted_id]["local_repository_ids"].append(local_repository_id)
            worktree["hosted_repository_id"] = hosted_id
        local_repository = graph["local_repositories"].setdefault(
            local_repository_id,
            {
                "id": local_repository_id,
                "common_git_dir": local_identity["common_git_dir"],
                "git_worktree_ids": [],
                "local_branch_ids": [],
                "hosted_repository_id": hosted_id,
            },
        )
        if worktree_id not in local_repository["git_worktree_ids"]:
            local_repository["git_worktree_ids"].append(worktree_id)
        current_branch_id: str | None = None
        for branch in git_data.get("other_branches", {}).get("branches", []):
            if not isinstance(branch, dict) or not isinstance(branch.get("name"), str):
                continue
            branch_id = local_branch_graph_id(local_repository_id, branch["name"])
            branch_record = graph["local_branches"].setdefault(
                branch_id,
                {
                    "id": branch_id,
                    "local_repository_id": local_repository_id,
                    **{key: value for key, value in branch.items() if key not in {"current", "pull_request", "linear"}},
                    "checked_out_worktree_ids": [],
                    "pull_request_ids": [],
                    "linear_issue_ids": [],
                    "pull_request_lookup_state": "not-requested",
                },
            )
            if branch_id not in local_repository["local_branch_ids"]:
                local_repository["local_branch_ids"].append(branch_id)
            if branch.get("current"):
                current_branch_id = branch_id
                worktree["current_branch_id"] = branch_id
                if worktree_id not in branch_record["checked_out_worktree_ids"]:
                    branch_record["checked_out_worktree_ids"].append(worktree_id)
        if isinstance(current_branch_id, str):
            current_branch = graph["local_branches"][current_branch_id]
            branch_history = record_worktree_branch_history(worktree_id, current_branch, observation["last_observed_at"])
            add_branch_activity(
                graph,
                worktree_id=worktree_id,
                branch_id=current_branch_id,
                branch_name_snapshot=str(current_branch.get("name") or ""),
                observed_head_sha=current_branch.get("head_sha"),
                observation=observation,
                current=True,
            )
            for historical_name, history in branch_history.items():
                historical_branch_id = local_branch_graph_id(local_repository_id, historical_name)
                historical_branch = graph["local_branches"].setdefault(
                    historical_branch_id,
                    {
                        "id": historical_branch_id,
                        "local_repository_id": local_repository_id,
                        "name": historical_name,
                        "remote": False,
                        "updated": history.get("updated"),
                        "updated_ts": history.get("updated_ts"),
                        "head": history.get("head"),
                        "head_sha": history.get("head_sha"),
                        "subject": history.get("subject"),
                        "linear_ids": extract_linear_ids(historical_name, history.get("subject")),
                        "checked_out_worktree_ids": [],
                        "pull_request_ids": [],
                        "linear_issue_ids": [],
                        "missing": True,
                    },
                )
                if historical_branch_id not in local_repository["local_branch_ids"]:
                    local_repository["local_branch_ids"].append(historical_branch_id)
                if historical_branch_id == current_branch_id:
                    historical_branch["missing"] = False
                    continue
                add_branch_activity(
                    graph,
                    worktree_id=worktree_id,
                    branch_id=historical_branch_id,
                    branch_name_snapshot=historical_name,
                    observed_head_sha=history.get("head_sha"),
                    observation={
                        **observation,
                        "first_observed_at": history["first_observed_at"],
                        "last_observed_at": history["last_observed_at"],
                    },
                    current=False,
                )
        if hosted_id:
            current_branch_id = worktree.get("current_branch_id")
            if isinstance(current_branch_id, str) and current_branch_id in graph["local_branches"]:
                # This is canonical branch enrichment, despite the old helper name. It resolves
                # local PR refs, subject markers, and branch heads into the branch entity so the
                # graph can rank and render it without a session-level project projection.
                current_pull_request = current_branch_pull_request(git_data, cache, allow_network=allow_network)
                if isinstance(current_pull_request, dict):
                    associate_branch_pull_request(
                        graph,
                        hosted_repository_id=hosted_id,
                        local_branch_id=current_branch_id,
                        pull_request=current_pull_request,
                        cache=cache,
                        allow_network=allow_network,
                    )
            enrich_branch_pull_requests(git_data, cache, allow_network=allow_network)
            enrich_branch_linear_metadata(git_data, cache, allow_network=allow_network)
            for branch in git_data.get("other_branches", {}).get("branches", []):
                if not isinstance(branch, dict) or not isinstance(branch.get("name"), str):
                    continue
                branch_id = local_branch_graph_id(local_repository_id, branch["name"])
                if branch_id not in graph["local_branches"]:
                    continue
                graph["local_branches"][branch_id]["pull_request_lookup_state"] = branch_pr_lookup_state(
                    branch,
                    has_hosted_repository=True,
                    allow_network=allow_network,
                )
                associate_branch_linear_issues(
                    graph,
                    local_branch_id=branch_id,
                    identifiers=list(branch.get("linear_ids") or []),
                    cache=cache,
                    allow_network=allow_network,
                )
                pull_requests = branch.get("pull_requests") if isinstance(branch.get("pull_requests"), list) else [branch.get("pull_request")]
                for pull_request in pull_requests:
                    if not isinstance(pull_request, dict):
                        continue
                    associate_branch_pull_request(
                        graph,
                        hosted_repository_id=hosted_id,
                        local_branch_id=branch_id,
                        pull_request=pull_request,
                        cache=cache,
                        allow_network=allow_network,
                    )
        else:
            for branch_id in local_repository["local_branch_ids"]:
                graph["local_branches"][branch_id]["pull_request_lookup_state"] = "not-requested"
        worktree["git"] = {
            key: copy.deepcopy(value)
            for key, value in git_data.items()
            if key not in {"other_branches", "local_repository"}
        }
    annotate_worktree_activity(graph)
    validate_work_graph(graph)
    return graph


def validate_work_graph(graph: dict[str, Any]) -> None:
    """Fail development/test builds loudly if a normalized association points at a missing entity."""
    for worktree in graph["git_worktrees"].values():
        assert worktree["local_repository_id"] in graph["local_repositories"]
        activity_ids = worktree["branch_activity_ids"]
        assert len(activity_ids) == len(set(activity_ids))
        current_activities = [
            graph["worktree_branch_activity"][activity_id]
            for activity_id in activity_ids
            if graph["worktree_branch_activity"][activity_id]["current"]
        ]
        assert len(current_activities) <= 1
    for branch in graph["local_branches"].values():
        assert branch["local_repository_id"] in graph["local_repositories"]
        assert all(pr_id in graph["pull_requests"] for pr_id in branch["pull_request_ids"])
    for activity in graph["worktree_branch_activity"].values():
        assert activity["git_worktree_id"] in graph["git_worktrees"]
        branch_id = activity["local_branch_id"]
        assert branch_id is None or branch_id in graph["local_branches"]
        assert isinstance(activity["branch_name_snapshot"], str) and activity["branch_name_snapshot"]
    for pull_request in graph["pull_requests"].values():
        assert pull_request["hosted_repository_id"] in graph["hosted_repositories"]
        assert all(branch_id in graph["local_branches"] for branch_id in pull_request["local_branch_ids"])


def annotate_worktree_activity(graph: dict[str, Any]) -> None:
    """Attach the old compact-summary ranking inputs to their canonical worktree owner."""
    for worktree in graph["git_worktrees"].values():
        observations = [
            graph["path_observations"][observation_id]
            for observation_id in worktree["path_observation_ids"]
            if observation_id in graph["path_observations"]
        ]
        worktree["activity_priority"] = min((int(observation["priority"]) for observation in observations), default=999)
        status_lines = worktree["git"].get("status")
        dirty_activity_ts = repo_dirty_activity_ts(worktree["root"], status_lines if isinstance(status_lines, list) else [])
        commit_activity_ts = repo_commit_activity_ts(worktree["root"])
        worktree["activity_ts"] = max(dirty_activity_ts, commit_activity_ts)
        worktree["activity_source"] = "dirty" if dirty_activity_ts >= commit_activity_ts and dirty_activity_ts > 0 else ("commit" if commit_activity_ts > 0 else "")
        current_branch_id = worktree.get("current_branch_id")
        current_branch = graph["local_branches"].get(current_branch_id) if isinstance(current_branch_id, str) else None
        worktree["has_current_pull_request"] = bool(current_branch and current_branch["pull_request_ids"])


def activity_work_summary_from_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Make the small activity/run-history projection from canonical graph edges only.

    This deliberately is not a session ``project``: the graph remains the complete
    owner. The projection selects the worktree with the strongest observed evidence
    for compact activity surfaces while retaining every observed repository path.
    """
    if graph.get("loading"):
        return {"git": {}, "pull_request": None, "linear": [], "repos": [], "loading": True}
    # Lightweight session metadata and narrow test doubles intentionally omit graph entities. They
    # have no compact work projection yet; treat them as empty rather than making a badge refresh
    # depend on a second compatibility collector.
    if not isinstance(graph.get("git_worktrees"), dict):
        return {"git": {}, "pull_request": None, "linear": [], "repos": [], "loading": False}
    worktrees = [
        worktree for _index, worktree in sorted(
            enumerate(graph["git_worktrees"].values()),
            key=lambda item: (
                int(item[1].get("activity_priority", 999)),
                -int(item[1].get("has_current_pull_request", False)),
                -float(item[1].get("activity_ts") or 0),
                item[0],
            ),
        )
    ]
    if not worktrees:
        return {"git": {}, "pull_request": None, "linear": [], "repos": [], "loading": False}
    repos: list[dict[str, Any]] = []
    for index, worktree in enumerate(worktrees):
        local_repository = graph["local_repositories"][worktree["local_repository_id"]]
        current_branch_id = worktree.get("current_branch_id")
        git_data = {
            **copy.deepcopy(worktree["git"]),
            "root": worktree["root"],
            "local_repository": {
                "id": local_repository["id"],
                "common_git_dir": local_repository["common_git_dir"],
                "worktree_id": worktree["id"],
                "worktree_git_dir": worktree["git_dir"],
                "kind": worktree["kind"],
            },
        }
        current_pr = None
        if isinstance(current_branch_id, str):
            branch = graph["local_branches"].get(current_branch_id)
            if branch:
                git_data["branch"] = branch.get("name")
                for pr_id in branch["pull_request_ids"]:
                    current_pr = copy.deepcopy(graph["pull_requests"][pr_id])
                    break
        repos.append({"root": worktree["root"], "cwd": worktree["root"], "branch": git_data.get("branch"), "ahead": git_data.get("ahead"), "behind": git_data.get("behind"), "dirty_count": git_data.get("dirty_count", 0), "activity_ts": worktree["activity_ts"], "activity_source": worktree["activity_source"], "github_repo": git_data.get("github_repo"), "worktree": git_data.get("worktree"), "local_repository": git_data["local_repository"], "selected": index == 0, "git": git_data, "pull_request": current_pr})
    selected = repos[0]
    selected_pr = selected["pull_request"]
    linear_ids = selected_pr.get("linear_issue_ids", []) if isinstance(selected_pr, dict) else []
    return {"git": selected["git"], "pull_request": selected_pr, "linear": [copy.deepcopy(graph["linear_issues"][linear_id]) for linear_id in linear_ids if linear_id in graph["linear_issues"]], "repos": repos, "loading": False}

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
        known: list[dict[str, Any]] = []
        if isinstance(number, int):
            by_number = pull_request_by_number_or_fallback(
                repo,
                number,
                cache,
                allow_network,
                "local-ref",
                local_pr.get("title") if isinstance(local_pr.get("title"), str) else branch.get("subject"),
            )
            if isinstance(by_number, dict):
                known.append(by_number)
        # No local (#N) marker — that only appears after a squash-merge, so an OPEN PR on this branch
        # has none and the by-number path above can't find it. Resolve it by HEAD BRANCH instead,
        # mirroring current_branch_pull_request's fallback, so YO!info surfaces open PRs on non-current
        # branches (not just merged ones). Skip the current branch (current_branch_pull_request already
        # resolved its PR) and main/HEAD (no feature PR). github_pull_request_by_branch is cache-keyed,
        # so this is bounded to at most OTHER_BRANCH_LIMIT branch lookups per repo per cache window.
        name = branch.get("name")
        if not isinstance(name, str) or name in MAIN_BRANCHES or name == "HEAD":
            branch["pull_request_lookup_state"] = "ready" if known else "none"
            if known:
                branch["pull_requests"] = known
                branch["pull_request"] = known[0]
            continue
        if branch.get("current") and not known:
            # The current branch is resolved by current_branch_pull_request(), whose local-ref/subject/
            # branch-name fallbacks carry richer context than a duplicate by-head lookup.
            branch["pull_request_lookup_state"] = "not-requested" if not allow_network else "none"
            continue
        found, lookup_state = branch_pull_requests(repo, name, cache, allow_network=allow_network, known=known)
        branch["pull_request_lookup_state"] = lookup_state
        if found:
            branch["pull_requests"] = found
            branch["pull_request"] = found[0]

def enrich_branch_linear_metadata(git_data: dict[str, Any], cache: MetadataCache, allow_network: bool = True) -> None:
    inventory = git_data.get("other_branches")
    branches = inventory.get("branches") if isinstance(inventory, dict) else None
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        pull_requests = branch.get("pull_requests") if isinstance(branch.get("pull_requests"), list) else [branch.get("pull_request")]
        pr_linear_ids = [
            identifier
            for pull_request in pull_requests
            if isinstance(pull_request, dict)
            for identifier in (pull_request.get("linear_ids") if isinstance(pull_request.get("linear_ids"), list) else [])
        ]
        linear_ids = extract_linear_ids(
            branch.get("name") if isinstance(branch.get("name"), str) else None,
            branch.get("subject") if isinstance(branch.get("subject"), str) else None,
            *[
                value
                for pull_request in pull_requests
                if isinstance(pull_request, dict)
                for value in (pull_request.get("title"), pull_request.get("description"))
                if isinstance(value, str)
            ],
            " ".join(str(item) for item in pr_linear_ids),
        )
        if not linear_ids:
            branch["linear"] = []
            continue
        branch["linear_ids"] = linear_ids
        branch["linear"] = [cached_linear_issue_metadata(identifier, cache, allow_network=allow_network) for identifier in linear_ids]


def cached_linear_issue_metadata(identifier: str, cache: MetadataCache, *, allow_network: bool) -> dict[str, Any]:
    key = f"linear-issue:{identifier}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return copy.deepcopy(cached)
    value = linear_issue_metadata(identifier, cache, allow_network=allow_network)
    cache.set(key, copy.deepcopy(value))
    return value

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


def repo_summary(
    cwd: str | None,
    branch_limit: int | None = OTHER_BRANCH_LIMIT,
) -> dict[str, Any] | None:
    # C9: a per-repo local summary. It stays network-free, but includes branch inventory so YO!info can
    # show every branch in every repo a session touches; only the primary repo's PRs are enriched eagerly.
    root_text = git_root_for_cwd(cwd)
    if root_text is None:
        return None
    base = git_metadata_base(root_text, branch_limit=branch_limit)
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


def indexed_repo_roots(indexed_dirs: list[str] | None = None) -> list[str]:
    raw_dirs = indexed_dirs
    if raw_dirs is None:
        raw_dirs = settings_payload().get("settings", {}).get("file_explorer", {}).get("indexed_dirs", [])
    if not isinstance(raw_dirs, list):
        return []
    cache_key = tuple(str(item).strip() for item in raw_dirs if isinstance(item, str) and str(item).strip())
    now = time.monotonic()
    with _INDEXED_REPO_ROOTS_CACHE_LOCK:
        cached = _INDEXED_REPO_ROOTS_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < INDEXED_REPO_ROOTS_CACHE_SECONDS:
            return list(cached[1])

        # Keep the lock through the walk: one slow first reader is acceptable;
        # N simultaneous readers recursively walking the same configured root is
        # not.  The result is small (repository paths only), and callers receive
        # their own list copy below.
        roots = _discover_indexed_repo_roots(raw_dirs)
        _INDEXED_REPO_ROOTS_CACHE[cache_key] = (time.monotonic(), roots)
        if len(_INDEXED_REPO_ROOTS_CACHE) > 64:
            oldest_key = min(_INDEXED_REPO_ROOTS_CACHE, key=lambda key: _INDEXED_REPO_ROOTS_CACHE[key][0])
            _INDEXED_REPO_ROOTS_CACHE.pop(oldest_key, None)
        return list(roots)


def _discover_indexed_repo_roots(raw_dirs: list[str]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()

    def add_repo(path: Path) -> bool:
        root = str(path.resolve()) if (path / ".git").exists() else git_root_for_cwd(str(path))
        if not root or root in seen:
            return False
        seen.add(root)
        roots.append(root)
        return True

    for raw in raw_dirs:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            indexed_root = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if not indexed_root.is_dir():
            continue
        if add_repo(indexed_root):
            continue
        for current, dirs, files in os.walk(indexed_root, topdown=True, followlinks=False):
            current_path = Path(current)
            if ".git" in dirs or ".git" in files:
                add_repo(current_path)
                dirs[:] = []
                continue
            dirs[:] = sorted(name for name in dirs if name not in SEARCH_SKIP_DIRS)
    return roots


def indexed_repo_summaries(
    indexed_dirs: list[str] | None = None,
    cache: MetadataCache | None = None,
    allow_network: bool = False,
) -> list[dict[str, Any]]:
    metadata_cache = cache or MetadataCache()
    summaries: list[dict[str, Any]] = []
    for root in indexed_repo_roots(indexed_dirs):
        summary = repo_summary(root, branch_limit=None)
        if not summary:
            continue
        summary["indexed"] = True
        summary["primary"] = False
        enrich_branch_pull_requests(summary, metadata_cache, allow_network=allow_network)
        summaries.append(summary)
    return summaries


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


def pane_signature(pane: TmuxPaneInfo) -> tuple[Any, ...]:
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

def window_metadata(info: SessionInfo) -> list[dict[str, Any]]:
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

def current_branch_pull_request(git_data: dict[str, Any], cache: MetadataCache, allow_network: bool = True) -> dict[str, Any] | None:
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
    work_graph = session_work_graph(info, metadata_cache, allow_network=allow_network) if include_metadata else empty_work_graph(loading=True)
    window_rows = window_metadata(info)
    return {
        "session": info.session,
        "panes": [asdict(pane) for pane in info.panes],
        "selected_pane": asdict(info.selected_pane) if info.selected_pane else None,
        "agents": [
            {
                **asdict(agent),
                **(
                    message_fields(
                        "error",
                        "transcript.error.unavailable",
                        agent.error,
                        {"error": agent.error},
                    )
                    if agent.error
                    else {}
                ),
            }
            for agent in info.agents
        ],
        "window_metadata": window_rows,
        "transcript_mtime": transcript_mtime,
        "work_graph": work_graph,
        "metadata_loading": not include_metadata,
    }

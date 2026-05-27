from __future__ import annotations

from .common import *
from .common import _CACHE_MISS
from .sessions import discover_sessions
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

    def set(self, key: str, value: Any) -> None:
        with self.lock:
            self.values[key] = (time.time() + self.ttl_seconds, value)

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

    branch = git_data.get("branch")
    if not isinstance(branch, str) or branch in MAIN_BRANCHES or branch == "HEAD":
        return None
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

def github_pull_request_url(repo: dict[str, str], number: int) -> str:
    return f"{repo['url']}/pull/{number}"

def cached_metadata(
    cache: MetadataCache,
    key: str,
    allow_network: bool,
    load: Callable[[], Any],
) -> Any:
    cached = cache.get(key)
    if cached is not _CACHE_MISS:
        return cached
    if not allow_network:
        return None
    value = load()
    cache.set(key, value)
    return value

def github_pull_request_by_number(
    repo: dict[str, str],
    number: int,
    cache: MetadataCache,
    allow_network: bool = True,
) -> dict[str, Any] | None:
    key = f"github-pr:{repo['owner']}/{repo['name']}:{number}"

    def load() -> dict[str, Any] | None:
        path = f"/repos/{quote(repo['owner'])}/{quote(repo['name'])}/pulls/{number}"
        payload = github_api_get(path)
        value = normalize_github_pull_request(payload, repo, "github-api") if isinstance(payload, dict) else None
        if value is not None:
            enrich_github_pull_request(value, repo, cache)
        return value

    return cached_metadata(cache, key, allow_network, load)

def github_pull_request_by_branch(
    repo: dict[str, str],
    branch: str,
    cache: MetadataCache,
    allow_network: bool = True,
) -> dict[str, Any] | None:
    key = f"github-pr-branch:{repo['owner']}/{repo['name']}:{branch}"

    def load() -> dict[str, Any] | None:
        query = urlencode({"head": f"{repo['owner']}:{branch}", "state": "all", "per_page": "10"})
        payload = github_api_get(f"/repos/{quote(repo['owner'])}/{quote(repo['name'])}/pulls?{query}")
        value = None
        if isinstance(payload, list):
            pull_requests = [item for item in payload if isinstance(item, dict)]
            selected = next((item for item in pull_requests if item.get("state") == "open"), None)
            if selected is None and pull_requests:
                selected = pull_requests[0]
            if selected is not None:
                value = normalize_github_pull_request(selected, repo, "github-api")
                if value is not None:
                    enrich_github_pull_request(value, repo, cache)
        return value

    return cached_metadata(cache, key, allow_network, load)

def normalize_github_pull_request(payload: dict[str, Any], repo: dict[str, str], source: str) -> dict[str, Any] | None:
    number = payload.get("number")
    if not isinstance(number, int):
        return None
    title = payload.get("title") if isinstance(payload.get("title"), str) else None
    body = payload.get("body") if isinstance(payload.get("body"), str) else None
    state = payload.get("state") if isinstance(payload.get("state"), str) else None
    merged = payload.get("merged") is True
    merged_at = payload.get("merged_at") if isinstance(payload.get("merged_at"), str) else None
    draft = payload.get("draft") is True
    head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
    head_sha = head.get("sha") if isinstance(head.get("sha"), str) else None
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    author_login = user.get("login") if isinstance(user.get("login"), str) else None
    url = payload.get("html_url") if isinstance(payload.get("html_url"), str) else github_pull_request_url(repo, number)
    result = {
        "number": number,
        "title": title,
        "state": state,
        "merged": merged,
        "merged_at": merged_at,
        "draft": draft,
        "author_login": author_login,
        "head_sha": head_sha,
        "url": url,
        "description": compact_description(body),
        "linear_ids": extract_linear_ids(title, body),
        "checks": github_checks_unknown(),
        "source": source,
    }
    result["status_label"] = pull_request_status_label(result)
    return result

def enrich_github_pull_request(value: dict[str, Any], repo: dict[str, str], cache: MetadataCache) -> None:
    if value.get("merged") is True or value.get("draft") is True or value.get("state") == "closed":
        value["status_label"] = pull_request_status_label(value)
        return
    head_sha = value.get("head_sha")
    if isinstance(head_sha, str) and head_sha:
        value["checks"] = github_commit_checks(repo, head_sha, cache)
    value["status_label"] = pull_request_status_label(value)

def pull_request_status_label(value: dict[str, Any]) -> str:
    if value.get("draft") is True:
        return "draft"
    if value.get("merged") is True or isinstance(value.get("merged_at"), str):
        return "merged"
    state = value.get("state")
    if state == "closed":
        return "closed"
    if state == "open":
        checks = value.get("checks")
        check_state = checks.get("state") if isinstance(checks, dict) else None
        if check_state == "passing":
            return "open · CI passing"
        if check_state == "failing":
            return "open · CI failing"
        if check_state == "pending":
            return "open · CI pending"
        return "open"
    return state if isinstance(state, str) and state else "unknown"

def github_checks_unknown() -> dict[str, Any]:
    return {
        "state": "unknown",
        "summary": "CI unknown",
        "total": 0,
        "passing": 0,
        "failing": [],
        "pending": [],
        "check_runs": [],
        "statuses": [],
    }

def github_commit_checks(repo: dict[str, str], head_sha: str, cache: MetadataCache) -> dict[str, Any]:
    key = f"github-checks:{repo['owner']}/{repo['name']}:{head_sha}"
    cached = cache.get(key)
    if cached is not _CACHE_MISS:
        return cached
    owner = quote(repo["owner"])
    name = quote(repo["name"])
    sha = quote(head_sha)
    check_runs = github_api_get(f"/repos/{owner}/{name}/commits/{sha}/check-runs?per_page=100")
    statuses = github_api_get(f"/repos/{owner}/{name}/commits/{sha}/status")
    value = summarize_github_checks(check_runs, statuses)
    cache.set(key, value)
    return value

def summarize_github_checks(check_runs_payload: Any, statuses_payload: Any) -> dict[str, Any]:
    check_runs: list[dict[str, Any]] = []
    if isinstance(check_runs_payload, dict) and isinstance(check_runs_payload.get("check_runs"), list):
        for item in check_runs_payload["check_runs"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") if isinstance(item.get("name"), str) else "check"
            status = item.get("status") if isinstance(item.get("status"), str) else None
            conclusion = item.get("conclusion") if isinstance(item.get("conclusion"), str) else None
            url = item.get("html_url") if isinstance(item.get("html_url"), str) else None
            check_runs.append({"name": name, "status": status, "conclusion": conclusion, "url": url})

    statuses: list[dict[str, Any]] = []
    combined_state = None
    if isinstance(statuses_payload, dict):
        combined_state = statuses_payload.get("state") if isinstance(statuses_payload.get("state"), str) else None
        for item in statuses_payload.get("statuses", []):
            if not isinstance(item, dict):
                continue
            context = item.get("context") if isinstance(item.get("context"), str) else "status"
            state = item.get("state") if isinstance(item.get("state"), str) else None
            url = item.get("target_url") if isinstance(item.get("target_url"), str) else None
            statuses.append({"name": context, "state": state, "url": url})

    failing = failing_github_checks(check_runs, statuses, combined_state)
    pending = pending_github_checks(check_runs, statuses, combined_state)
    total = len(check_runs) + len(statuses)
    passing = passing_github_check_count(check_runs, statuses, combined_state)
    if failing:
        state = "failing"
    elif pending:
        state = "pending"
    elif total > 0 or combined_state == "success":
        state = "passing"
    else:
        state = "unknown"
    return {
        "state": state,
        "summary": f"CI {state}" if state != "unknown" else "CI unknown",
        "total": total,
        "passing": passing,
        "failing": failing[:8],
        "pending": pending[:8],
        "check_runs": check_runs[:40],
        "statuses": statuses[:40],
    }

def failing_github_checks(
    check_runs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    combined_state: str | None,
) -> list[dict[str, str]]:
    failed_conclusions = {"action_required", "cancelled", "failure", "startup_failure", "timed_out"}
    result: list[dict[str, str]] = []
    for item in check_runs:
        conclusion = item.get("conclusion")
        if isinstance(conclusion, str) and conclusion in failed_conclusions:
            result.append({"name": str(item.get("name") or "check"), "state": conclusion})
    for item in statuses:
        state = item.get("state")
        if isinstance(state, str) and state in {"error", "failure"}:
            result.append({"name": str(item.get("name") or "status"), "state": state})
    if combined_state in {"error", "failure"} and not result:
        result.append({"name": "combined status", "state": combined_state})
    return result

def pending_github_checks(
    check_runs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    combined_state: str | None,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in check_runs:
        status = item.get("status")
        if isinstance(status, str) and status != "completed":
            result.append({"name": str(item.get("name") or "check"), "state": status})
    for item in statuses:
        state = item.get("state")
        if state == "pending":
            result.append({"name": str(item.get("name") or "status"), "state": state})
    if combined_state == "pending" and not result:
        result.append({"name": "combined status", "state": combined_state})
    return result

def passing_github_check_count(
    check_runs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    combined_state: str | None,
) -> int:
    passed_conclusions = {"success", "neutral", "skipped"}
    count = 0
    for item in check_runs:
        conclusion = item.get("conclusion")
        if isinstance(conclusion, str) and conclusion in passed_conclusions:
            count += 1
    for item in statuses:
        if item.get("state") == "success":
            count += 1
    if count == 0 and combined_state == "success":
        return 1
    return count

def github_api_get(path: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "YOLOmux",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"token {token}"
    return http_json(f"{GITHUB_API_ROOT}{path}", headers=headers, timeout=HTTP_METADATA_TIMEOUT_SECONDS)

def github_token() -> str | None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    path = Path.home() / ".config" / "gh" / "hosts.yml"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("oauth_token:"):
                value = stripped.split(":", 1)[1].strip()
                if value:
                    return value
    except OSError:
        return None
    return None

def linear_issue_metadata(identifier: str, cache: MetadataCache, allow_network: bool = True) -> dict[str, Any]:
    key = f"linear:{identifier}"
    cached = cache.get(key)
    if cached is not _CACHE_MISS:
        return cached
    if not allow_network:
        return fallback_linear_issue(identifier)
    value = linear_issue_from_api(identifier) or fallback_linear_issue(identifier)
    cache.set(key, value)
    return value

def linear_issue_from_api(identifier: str) -> dict[str, Any] | None:
    token = linear_key()
    if not token:
        return None
    payload = {
        "query": (
            "query($id: String!) { issue(id: $id) { "
            "identifier title url state { name } "
            "} }"
        ),
        "variables": {"id": identifier},
    }
    response = http_json(
        LINEAR_API_URL,
        headers={"Authorization": token, "Content-Type": "application/json"},
        payload=payload,
        timeout=HTTP_METADATA_TIMEOUT_SECONDS,
    )
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    issue = data.get("issue") if isinstance(data, dict) else None
    if not isinstance(issue, dict):
        return None
    state = issue.get("state")
    return {
        "identifier": issue.get("identifier") if isinstance(issue.get("identifier"), str) else identifier,
        "title": issue.get("title") if isinstance(issue.get("title"), str) else None,
        "state": state.get("name") if isinstance(state, dict) and isinstance(state.get("name"), str) else None,
        "url": issue.get("url") if isinstance(issue.get("url"), str) else linear_issue_url(identifier),
        "source": "linear-api",
    }

def fallback_linear_issue(identifier: str) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "title": None,
        "state": None,
        "url": linear_issue_url(identifier),
        "source": "local-id",
    }

def linear_issue_url(identifier: str) -> str:
    base_url = os.environ.get("YOLOMUX_LINEAR_ISSUE_BASE_URL", DEFAULT_LINEAR_ISSUE_BASE_URL).rstrip("/")
    return f"{base_url}/{quote(identifier)}"

def linear_key() -> str | None:
    token = os.environ.get("LINEAR_KEY")
    if token:
        return token.strip()
    path = Path.home() / ".config" / "linear.key"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None

def http_json(
    url: str,
    headers: dict[str, str],
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

def extract_linear_ids(*texts: str | None) -> list[str]:
    identifiers: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in LINEAR_ID_RE.finditer(text):
            identifier = match.group(0)
            if identifier in seen:
                continue
            seen.add(identifier)
            identifiers.append(identifier)
    return identifiers

def compact_description(text: str | None, limit: int = 480) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<!--"):
            return truncate_text(re.sub(r"\s+", " ", stripped), limit)
    return None

def session_to_json(info: SessionInfo, metadata_cache: MetadataCache, allow_network: bool = True) -> dict[str, Any]:
    return {
        "session": info.session,
        "panes": [asdict(pane) for pane in info.panes],
        "selected_pane": asdict(info.selected_pane) if info.selected_pane else None,
        "agents": [asdict(agent) for agent in info.agents],
        "project": session_project_metadata(info, metadata_cache, allow_network=allow_network),
    }

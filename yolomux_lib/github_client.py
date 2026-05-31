from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from typing import Callable
from urllib.parse import quote
from urllib.parse import urlencode

from .common import GITHUB_API_ROOT
from .common import HTTP_METADATA_TIMEOUT_SECONDS
from .common import LINEAR_ID_RE
from .common import _CACHE_MISS
from .common import truncate_text


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


def github_pull_request_url(repo: dict[str, str], number: int) -> str:
    return f"{repo['url']}/pull/{number}"


def github_pull_request_payload(repo: dict[str, str], number: int) -> Any:
    path = f"/repos/{quote(repo['owner'])}/{quote(repo['name'])}/pulls/{number}"
    return github_api_get(path)


def github_pull_requests_by_branch_payload(repo: dict[str, str], branch: str) -> Any:
    query = urlencode({"head": f"{repo['owner']}:{branch}", "state": "all", "per_page": "10"})
    path = f"/repos/{quote(repo['owner'])}/{quote(repo['name'])}/pulls?{query}"
    return github_api_get(path)


def github_commit_check_payloads(repo: dict[str, str], head_sha: str) -> tuple[Any, Any]:
    owner = quote(repo["owner"])
    name = quote(repo["name"])
    sha = quote(head_sha)
    check_runs = github_api_get(f"/repos/{owner}/{name}/commits/{sha}/check-runs?per_page=100")
    statuses = github_api_get(f"/repos/{owner}/{name}/commits/{sha}/status")
    return check_runs, statuses


def cached_metadata(
    cache: Any,
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
    cache: Any,
    allow_network: bool = True,
) -> dict[str, Any] | None:
    key = f"github-pr:{repo['owner']}/{repo['name']}:{number}"

    def load() -> dict[str, Any] | None:
        payload = github_pull_request_payload(repo, number)
        value = normalize_github_pull_request(payload, repo, "github-api") if isinstance(payload, dict) else None
        if value is not None:
            enrich_github_pull_request(value, repo, cache)
        return value

    return cached_metadata(cache, key, allow_network, load)


def github_pull_request_by_branch(
    repo: dict[str, str],
    branch: str,
    cache: Any,
    allow_network: bool = True,
) -> dict[str, Any] | None:
    key = f"github-pr-branch:{repo['owner']}/{repo['name']}:{branch}"

    def load() -> dict[str, Any] | None:
        payload = github_pull_requests_by_branch_payload(repo, branch)
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


def enrich_github_pull_request(value: dict[str, Any], repo: dict[str, str], cache: Any) -> None:
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


def github_commit_checks(repo: dict[str, str], head_sha: str, cache: Any) -> dict[str, Any]:
    key = f"github-checks:{repo['owner']}/{repo['name']}:{head_sha}"
    cached = cache.get(key)
    if cached is not _CACHE_MISS:
        return cached
    check_runs, statuses = github_commit_check_payloads(repo, head_sha)
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

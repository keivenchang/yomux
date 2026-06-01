from yolomux_lib import github_client
from yolomux_lib import linear_client
from yolomux_lib import metadata
from yolomux_lib.common import _CACHE_MISS
from yolomux_lib.metadata import MetadataCache
from yolomux_lib.metadata import extract_linear_ids
from yolomux_lib.metadata import github_checks_unknown
from yolomux_lib.metadata import linear_issue_metadata
from yolomux_lib.metadata import project_pull_request
from yolomux_lib.metadata import summarize_github_checks


REPO = {
    "owner": "ai-project",
    "name": "project",
    "url": "https://github.com/ai-project/project",
}


def test_main_branch_does_not_inherit_merged_pr_from_head_subject():
    git_data = {
        "github_repo": REPO,
        "branch": "main",
        "head": "747c3fd0c6 ci: Update the dep for the whl publish to be automated (#9961)",
        "head_sha": "747c3fd0c66278bb324c9106dac21dfc1eab44aa",
    }

    assert project_pull_request(git_data, MetadataCache(), allow_network=False) is None


def test_feature_branch_can_use_head_subject_pr_hint():
    git_data = {
        "github_repo": REPO,
        "branch": "keivenc/example",
        "head": "abc1234567 fix(parser): parse dangling reasoning end markers (#9981)",
        "head_sha": "abc123456789",
    }

    pr = project_pull_request(git_data, MetadataCache(), allow_network=False)

    assert pr is not None
    assert pr["number"] == 9981
    assert pr["source"] == "head-subject"


def test_metadata_public_helpers_remain_available_after_client_split():
    assert github_checks_unknown()["state"] == "unknown"
    assert extract_linear_ids("OPS-123 is linked to INFRA-44 and OPS-123") == ["OPS-123", "INFRA-44"]


def test_metadata_cache_expires_entries(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(metadata.time, "time", lambda: now)
    cache = MetadataCache(ttl_seconds=5)

    cache.set("key", {"value": 1})
    assert cache.get("key") == {"value": 1}

    now = 1006.0
    assert cache.get("key") is _CACHE_MISS


def test_github_pr_fetch_path_uses_cache_and_enriches_checks(monkeypatch):
    payload_calls = []
    check_calls = []

    def fake_pr_payload(_repo, number):
        payload_calls.append(number)
        return {
            "number": number,
            "title": "Fix tabs OPS-123",
            "body": "Uses OPS-123",
            "state": "open",
            "merged": False,
            "draft": False,
            "head": {"sha": "abc123"},
            "html_url": "https://github.com/ai-project/project/pull/42",
            "user": {"login": "keivenc"},
        }

    def fake_checks(_repo, head_sha):
        check_calls.append(head_sha)
        return (
            {"check_runs": [{"name": "unit", "status": "completed", "conclusion": "success"}]},
            {"state": "success", "statuses": []},
        )

    monkeypatch.setattr(github_client, "github_pull_request_payload", fake_pr_payload)
    monkeypatch.setattr(github_client, "github_commit_check_payloads", fake_checks)
    cache = MetadataCache()

    first = github_client.github_pull_request_by_number(REPO, 42, cache, allow_network=True)
    second = github_client.github_pull_request_by_number(REPO, 42, cache, allow_network=True)

    assert first == second
    assert first["number"] == 42
    assert first["source"] == "github-api"
    assert first["linear_ids"] == ["OPS-123"]
    assert first["checks"]["state"] == "passing"
    assert first["status_label"] == "open · CI passing"
    assert payload_calls == [42]
    assert check_calls == ["abc123"]


def test_linear_issue_metadata_uses_api_and_cache(monkeypatch):
    calls = []

    def fake_issue(identifier):
        calls.append(identifier)
        return {
            "identifier": identifier,
            "title": "Fix tabs",
            "state": "In Progress",
            "url": f"https://linear.app/dynamo/issue/{identifier}",
            "source": "linear-api",
        }

    monkeypatch.setattr(linear_client, "linear_issue_from_api", fake_issue)
    cache = MetadataCache()

    first = linear_issue_metadata("OPS-123", cache, allow_network=True)
    second = linear_issue_metadata("OPS-123", cache, allow_network=True)

    assert first == second
    assert first["source"] == "linear-api"
    assert first["title"] == "Fix tabs"
    assert calls == ["OPS-123"]


def test_github_check_summary_classifies_failure_and_pending_items():
    summary = summarize_github_checks(
        {"check_runs": [{"name": "unit", "status": "completed", "conclusion": "failure"}]},
        {"state": "pending", "statuses": [{"context": "deploy", "state": "pending"}]},
    )

    assert summary["state"] == "failing"
    assert summary["failing"] == [{"name": "unit", "state": "failure"}]
    assert summary["pending"] == [{"name": "deploy", "state": "pending"}]


def test_linear_issue_metadata_falls_back_without_network():
    issue = linear_issue_metadata("OPS-123", MetadataCache(), allow_network=False)

    assert issue["identifier"] == "OPS-123"
    assert issue["source"] == "local-id"
    assert issue["url"].endswith("/OPS-123")

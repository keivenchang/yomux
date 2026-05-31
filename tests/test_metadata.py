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

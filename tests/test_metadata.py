from yolomux_lib.metadata import MetadataCache
from yolomux_lib.metadata import project_pull_request


REPO = {
    "owner": "ai-dynamo",
    "name": "dynamo",
    "url": "https://github.com/ai-dynamo/dynamo",
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

from yolomux_lib import github_client
from yolomux_lib import linear_client
from yolomux_lib import metadata
from yolomux_lib.common import _CACHE_MISS
from yolomux_lib.common import tail_file_lines
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

    review_calls = []

    def fake_review_payload(_repo, number):
        review_calls.append(number)
        return {"data": {"repository": {"pullRequest": {
            "reviewDecision": "APPROVED",
            "latestOpinionatedReviews": {"nodes": [{"author": {"login": "alice"}, "state": "APPROVED"}]},
        }}}}

    monkeypatch.setattr(github_client, "github_pull_request_payload", fake_pr_payload)
    monkeypatch.setattr(github_client, "github_commit_check_payloads", fake_checks)
    monkeypatch.setattr(github_client, "github_pull_request_review_decision_payload", fake_review_payload)
    cache = MetadataCache()

    first = github_client.github_pull_request_by_number(REPO, 42, cache, allow_network=True)
    second = github_client.github_pull_request_by_number(REPO, 42, cache, allow_network=True)

    assert first == second
    assert first["number"] == 42
    assert first["source"] == "github-api"
    assert first["linear_ids"] == ["OPS-123"]
    assert first["checks"]["state"] == "passing"
    assert first["status_label"] == "open · CI passing"
    assert first["review_decision"] == "APPROVED"
    assert first["review_reviewers"] == [{"login": "alice", "state": "APPROVED"}]
    assert payload_calls == [42]
    assert check_calls == ["abc123"]
    # reviewDecision + reviewers are fetched once then served from cache (no second GraphQL round-trip).
    assert review_calls == [42]


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


def test_normalize_review_decision_reads_graphql_shape_and_ignores_garbage():
    assert github_client.normalize_review_decision(
        {"data": {"repository": {"pullRequest": {"reviewDecision": "CHANGES_REQUESTED"}}}}
    ) == "CHANGES_REQUESTED"
    # A null reviewDecision (no reviews required) and malformed payloads yield no badge.
    assert github_client.normalize_review_decision(
        {"data": {"repository": {"pullRequest": {"reviewDecision": None}}}}
    ) is None
    assert github_client.normalize_review_decision({"data": {"repository": None}}) is None
    assert github_client.normalize_review_decision(None) is None


def test_github_graphql_requires_token(monkeypatch):
    monkeypatch.setattr(github_client, "github_token", lambda: None)
    # Without a token GraphQL is skipped entirely (no network attempt), returning None.
    assert github_client.github_graphql("query{}", {}) is None


def test_review_decision_is_cached(monkeypatch):
    calls = []

    def fake_payload(_repo, number):
        calls.append(number)
        return {"data": {"repository": {"pullRequest": {"reviewDecision": "REVIEW_REQUIRED"}}}}

    monkeypatch.setattr(github_client, "github_pull_request_review_decision_payload", fake_payload)
    cache = MetadataCache()
    assert github_client.github_pull_request_review_decision(REPO, 7, cache) == "REVIEW_REQUIRED"
    assert github_client.github_pull_request_review_decision(REPO, 7, cache) == "REVIEW_REQUIRED"
    assert calls == [7]


def test_cached_metadata_gives_failures_a_short_negative_ttl():
    # DOIT.6 #71: a None result (transient 403/429/5xx/timeout, or genuinely no PR) is cached only
    # briefly so it retries soon, while a real value keeps the full positive TTL.
    cache = MetadataCache(ttl_seconds=300)
    github_client.cached_metadata(cache, "real", True, lambda: {"x": 1})
    github_client.cached_metadata(cache, "none", True, lambda: None)
    real_expiry = cache.values["real"][0]
    none_expiry = cache.values["none"][0]
    assert cache.values["none"][1] is None
    assert real_expiry - none_expiry > 60


def test_tail_file_lines_reads_a_bounded_window_from_eof(tmp_path):
    # DOIT.6 #72: return the last N lines without scanning the whole file.
    big = tmp_path / "transcript.jsonl"
    big.write_text("".join(f"row-{i}\n" for i in range(5000)))
    assert tail_file_lines(big, 3).splitlines() == ["row-4997", "row-4998", "row-4999"]
    small = tmp_path / "small.txt"
    small.write_text("a\nb\n")
    assert tail_file_lines(small, 10) == "a\nb\n"


def test_metadata_cache_is_bounded_and_sweeps_expired_on_write():
    # DOIT.6 #83: the cache stays bounded (cap + sweep expired) so dead branch/sha keys don't leak for
    # the process lifetime.
    cache = MetadataCache(ttl_seconds=300)
    for i in range(MetadataCache.MAX_ENTRIES + 1):
        cache.set(f"expired{i}", i, ttl=0)  # immediately expired
    cache.set("fresh", 1)
    assert len(cache.values) <= MetadataCache.MAX_ENTRIES
    assert "fresh" in cache.values
    # a large burst of live entries is also capped.
    cache2 = MetadataCache(ttl_seconds=300)
    for i in range(MetadataCache.MAX_ENTRIES + 100):
        cache2.set(f"k{i}", i)
    assert len(cache2.values) <= MetadataCache.MAX_ENTRIES


def test_parse_pull_request_ref_accepts_short_and_url_forms():
    # DOIT.29: owner/repo#N, owner/repo/N, and full PR URLs normalize to the canonical owner/repo#N.
    from yolomux_lib.metadata import parse_pull_request_ref

    for text in ("ai-dynamo/frontend-crates#18", "ai-dynamo/frontend-crates/18",
                 "https://github.com/ai-dynamo/frontend-crates/pull/18",
                 "  ai-dynamo/frontend-crates#18  ",
                 "https://github.com/ai-dynamo/frontend-crates/pull/18/files"):
        parsed = parse_pull_request_ref(text)
        assert parsed is not None, text
        assert parsed["owner"] == "ai-dynamo"
        assert parsed["name"] == "frontend-crates"
        assert parsed["number"] == 18
        assert parsed["ref"] == "ai-dynamo/frontend-crates#18"
        assert parsed["url"] == "https://github.com/ai-dynamo/frontend-crates/pull/18"


def test_parse_pull_request_ref_rejects_invalid():
    # DOIT.29: non-github hosts, missing/zero PR numbers, repo-only refs, and issue URLs are rejected.
    from yolomux_lib.metadata import parse_pull_request_ref

    for text in ("https://gitlab.com/owner/repo/pull/7", "owner/repo", "owner/repo#0",
                 "not a ref", "https://github.com/owner/repo/issues/3", "", None):
        assert parse_pull_request_ref(text) is None, text


def test_watched_pr_metadata_dedupes_caps_and_flags_invalid():
    # DOIT.29: dedupe by canonical ref, cap at WATCHED_PR_LIMIT, collect invalid entries, and (offline)
    # return the fallback PR shape carrying {ref, url}.
    from yolomux_lib.metadata import watched_pr_metadata, WATCHED_PR_LIMIT

    refs = ["owner/repo#1", "owner/repo#1", "bad ref", "owner/repo#2"]
    result = watched_pr_metadata(refs, MetadataCache(), allow_network=False)
    assert [pr["ref"] for pr in result["watched_prs"]] == ["owner/repo#1", "owner/repo#2"]
    assert result["invalid"] == ["bad ref"]
    assert result["truncated"] == 0
    first = result["watched_prs"][0]
    assert first["url"] == "https://github.com/owner/repo/pull/1"
    assert first["number"] == 1 and "status_label" in first

    big = [f"o/r#{i}" for i in range(1, WATCHED_PR_LIMIT + 6)]
    capped = watched_pr_metadata(big, MetadataCache(), allow_network=False)
    assert len(capped["watched_prs"]) == WATCHED_PR_LIMIT
    assert capped["truncated"] == 5


def test_candidate_session_cwds_prefers_live_pane_cwd_over_default(monkeypatch, tmp_path):
    # DOIT.32: the focused pane's live cwd (after `cd`) outranks the session-number default workdir, so
    # the project follows the pane instead of staying pinned to dynamoN.
    from yolomux_lib.common import PaneInfo, SessionInfo

    default_dir = tmp_path / "dynamo1"
    live_dir = tmp_path / "frontend-crates"
    default_dir.mkdir()
    live_dir.mkdir()
    monkeypatch.setattr(metadata, "session_workdir", lambda session: default_dir)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)

    pane = PaneInfo(
        session="1", window="0", pane="0", pane_id="%1", target="%1",
        current_path=str(live_dir), command="bash", active=True, window_active=True,
        title="", pid=1, process_label="bash",
    )
    info = SessionInfo(session="1", panes=[pane], selected_pane=pane, agents=[])

    candidates = metadata.candidate_session_cwds(info)
    live_resolved = str(live_dir.resolve())
    default_resolved = str(default_dir.resolve())
    assert candidates[0] == live_resolved, "the live pane cwd is the first candidate"
    assert default_resolved in candidates, "the session-number default remains a fallback"
    assert candidates.index(live_resolved) < candidates.index(default_resolved)


def test_candidate_session_cwds_falls_back_to_default_without_live_cwd(monkeypatch, tmp_path):
    # A session with no pane/agent cwd still falls back to the session-number default workspace.
    from yolomux_lib.common import SessionInfo

    default_dir = tmp_path / "dynamo1"
    default_dir.mkdir()
    monkeypatch.setattr(metadata, "session_workdir", lambda session: default_dir)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    assert metadata.candidate_session_cwds(info) == [str(default_dir.resolve())]

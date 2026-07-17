import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from yolomux_lib import github_client
from yolomux_lib import linear_client
from yolomux_lib import metadata
from yolomux_lib.common import _CACHE_MISS
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import TmuxPaneInfo
from yolomux_lib.common import tail_file_lines
from yolomux_lib.metadata import MetadataCache
from yolomux_lib.metadata import extract_linear_ids
from yolomux_lib.metadata import github_checks_unknown
from yolomux_lib.metadata import linear_issue_metadata
from yolomux_lib.metadata import parse_pull_request_ref
from yolomux_lib.metadata import current_branch_pull_request
from yolomux_lib.metadata import summarize_github_checks
from yolomux_lib.metadata import WATCHED_PR_LIMIT
from yolomux_lib.metadata import watched_pr_metadata
from yolomux_lib.sessions import discover_sessions

from _git_helpers import git as _git


def _pane(session, index, path):
    return TmuxPaneInfo(
        session=session, window="0", pane=str(index), pane_id=f"%{index}",
        target=f"{session}:0.{index}", current_path=str(path), command="zsh",
        active=index == 0, window_active=True, title="", pid=10 + index,
    )


def test_tmux_pane_info_is_the_explicit_backend_type_with_compatibility_alias():
    assert PaneInfo is TmuxPaneInfo
    backend_sources = [
        path for path in Path("yolomux_lib").glob("*.py")
        if path.name != "common.py"
    ]
    assert all(" PaneInfo" not in path.read_text(encoding="utf-8") for path in backend_sources)


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "init")


def test_session_work_graph_lists_every_touched_repo(tmp_path):
    # Every observed repository belongs to the one canonical graph; the compact activity projection
    # may rank one, but must not suppress the other.
    made = []
    for name in ("repoA", "repoB"):
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "init")
        base_branch = _git(repo, "branch", "--show-current").stdout.strip()
        _git(repo, "checkout", "-b", f"feature/{name}")
        (repo / "branch.txt").write_text(f"{name}\n", encoding="utf-8")
        _git(repo, "add", "branch.txt")
        _git(repo, "commit", "-m", f"feature {name}")
        _git(repo, "checkout", base_branch)
        made.append(repo)
    (made[1] / "f.txt").write_text("changed\n", encoding="utf-8")  # leave repoB dirty
    rootA = str(Path(made[0]).resolve())
    rootB = str(Path(made[1]).resolve())
    panes = [_pane("s1", 0, made[0]), _pane("s1", 1, made[1])]
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)
    work = metadata.activity_work_summary_from_graph(graph)
    by_root = {s["root"]: s for s in work["repos"]}

    assert rootA in by_root and rootB in by_root
    assert work["git"]["root"] in {rootA, rootB}
    assert by_root[rootB]["dirty_count"] == 1
    worktree = next(item for item in graph["git_worktrees"].values() if item["root"] == rootB)
    local_repository = graph["local_repositories"][worktree["local_repository_id"]]
    branches = {
        graph["local_branches"][branch_id]["name"]: graph["local_branches"][branch_id]
        for branch_id in local_repository["local_branch_ids"]
    }
    assert f"feature/{made[1].name}" in branches
    assert branches[f"feature/{made[1].name}"]["updated_ts"] > 0


def test_session_work_graph_single_repo_has_no_extra(tmp_path):
    # A single-repository session projects exactly one worktree.
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "init")
    root = str(Path(repo).resolve())
    panes = [_pane("s9", 0, repo)]
    info = SessionInfo(session="s9", panes=panes, selected_pane=panes[0], agents=[])

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)
    roots = {s["root"] for s in metadata.activity_work_summary_from_graph(graph)["repos"]}
    assert roots == {root}


def test_indexed_repo_summaries_discovers_repos_under_indexed_root_without_branch_cap(tmp_path):
    indexed = tmp_path / "indexed"
    indexed.mkdir()
    repo_a = indexed / "repo-a"
    repo_b = indexed / "repo-b"
    _init_repo(repo_a)
    _init_repo(repo_b)
    current_branch = _git(repo_a, "branch", "--show-current").stdout.strip()
    branch_names = [f"feature/{index}" for index in range(metadata.OTHER_BRANCH_LIMIT + 3)]
    for branch in branch_names:
        _git(repo_a, "branch", branch)
    _git(repo_a, "checkout", current_branch)

    summaries = metadata.indexed_repo_summaries([str(indexed)], cache=MetadataCache(), allow_network=False)
    by_root = {summary["root"]: summary for summary in summaries}
    repo_a_root = str(repo_a.resolve())
    repo_b_root = str(repo_b.resolve())
    repo_a_branches = {branch["name"] for branch in by_root[repo_a_root]["other_branches"]["branches"]}

    assert {repo_a_root, repo_b_root}.issubset(by_root)
    assert all(branch in repo_a_branches for branch in branch_names)
    assert by_root[repo_a_root]["other_branches"]["hidden_count"] == 0
    assert by_root[repo_a_root]["indexed"] is True


def test_indexed_repo_root_discovery_is_shared_across_metadata_requests(tmp_path, monkeypatch):
    indexed = tmp_path / "indexed"
    indexed.mkdir()
    calls = []
    metadata._INDEXED_REPO_ROOTS_CACHE.clear()
    monkeypatch.setattr(metadata, "git_root_for_cwd", lambda _path: None)

    def fake_walk(path, **_kwargs):
        calls.append(Path(path))
        return iter(())

    monkeypatch.setattr(metadata.os, "walk", fake_walk)
    try:
        assert metadata.indexed_repo_roots([str(indexed)]) == []
        assert metadata.indexed_repo_roots([str(indexed)]) == []
    finally:
        metadata._INDEXED_REPO_ROOTS_CACHE.clear()

    assert calls == [indexed.resolve()]


def test_unchanged_git_metadata_coalesces_real_subprocess_demand_and_refreshes_dirty_state(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    root = str(repo.resolve())
    real_git = metadata.git
    calls: list[tuple[str, ...]] = []
    calls_lock = threading.Lock()

    def counted_git(args, cwd, timeout=3.0):
        with calls_lock:
            calls.append(tuple(args))
        return real_git(args, cwd, timeout=timeout)

    monkeypatch.setattr(metadata, "git", counted_git)
    with metadata._GIT_METADATA_CACHE_LOCK:
        metadata._GIT_METADATA_CACHE.clear()
        metadata._GIT_METADATA_INFLIGHT.clear()

    cold = metadata.git_metadata_base(root)
    cold_count = len(calls)
    calls.clear()
    warm_results: list[dict | None] = []
    workers = [threading.Thread(target=lambda: warm_results.append(metadata.git_metadata_base(root))) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    warm_count = len(calls)

    assert len(warm_results) == 8
    assert warm_results == [cold] * 8
    assert cold_count >= 8
    assert warm_count <= 2, f"unchanged concurrent readers launched {warm_count} Git subprocesses"

    (repo / "f.txt").write_text("changed\n", encoding="utf-8")
    with metadata._GIT_METADATA_CACHE_LOCK:
        signature, value, _verified_at = metadata._GIT_METADATA_CACHE[(root, metadata.OTHER_BRANCH_LIMIT)]
        metadata._GIT_METADATA_CACHE[(root, metadata.OTHER_BRANCH_LIMIT)] = (signature, value, 0.0)
    calls.clear()
    changed = metadata.git_metadata_base(root)
    assert changed and changed["status_lines"] == [" M f.txt"]
    assert len(calls) >= 2, "a changed source signature must rebuild the Git snapshot"


def test_indexed_repo_summaries_excludes_remote_only_branches(tmp_path):
    indexed = tmp_path / "indexed"
    indexed.mkdir()
    repo = indexed / "repo"
    _init_repo(repo)
    current_branch = _git(repo, "branch", "--show-current").stdout.strip()
    remote_branch = "keivenchang/OPS-6052__fix-harmony-analysis-normal-text"
    _git(repo, "checkout", "-b", remote_branch)
    (repo / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(repo, "add", "remote.txt")
    _git(repo, "commit", "-m", "remote branch")
    remote_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", current_branch)
    _git(repo, "branch", "-D", remote_branch)
    _git(repo, "update-ref", f"refs/remotes/origin/{remote_branch}", remote_sha)

    summaries = metadata.indexed_repo_summaries([str(indexed)], cache=MetadataCache(), allow_network=False)
    branches = {branch["name"]: branch for branch in summaries[0]["other_branches"]["branches"]}

    assert remote_branch not in branches


def test_indexed_repo_summaries_includes_linked_worktree_complete_branch_inventory(tmp_path):
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    _init_repo(main)
    _git(main, "worktree", "add", "-q", str(wt), "-b", "feature-linked")

    summaries = metadata.indexed_repo_summaries([str(tmp_path)], cache=MetadataCache(), allow_network=False)
    by_root = {summary["root"]: summary for summary in summaries}
    wt_root = str(wt.resolve())
    wt_branches = {branch["name"]: branch for branch in by_root[wt_root]["other_branches"]["branches"]}

    assert wt_root in by_root
    assert set(wt_branches) == {"master", "feature-linked"}
    assert wt_branches["feature-linked"]["current"] is True
    assert wt_branches["master"]["current"] is False


def test_activity_work_summary_empty_shape_has_repositories_and_loading():
    info = SessionInfo(session="empty", panes=[], selected_pane=None, agents=[])
    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))

    assert work == {"git": {}, "pull_request": None, "linear": [], "repos": [], "loading": False}


def test_session_to_json_loading_uses_empty_work_graph_without_project_projection():
    info = SessionInfo(session="loading", panes=[], selected_pane=None, agents=[])

    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False, include_metadata=False)

    assert payload["work_graph"]["loading"] is True
    assert "project" not in payload
    assert payload["metadata_loading"] is True


def test_session_to_json_keeps_agent_transcript_error_structured():
    diagnostic = "transcript file disappeared"
    agent = AgentInfo(
        session="broken",
        kind="codex",
        pid=17,
        pane_target="broken:0.0",
        command="codex",
        cwd=None,
        status=None,
        session_id=None,
        transcript=None,
        error=diagnostic,
    )
    info = SessionInfo(session="broken", panes=[], selected_pane=None, agents=[agent])

    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False, include_metadata=False)

    assert payload["agents"][0]["error"] == diagnostic
    assert payload["agents"][0]["error_key"] == "transcript.error.unavailable"
    assert payload["agents"][0]["error_params"] == {"error": diagnostic}


def test_session_to_json_includes_window_metadata(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_repo(repo_a)
    _init_repo(repo_b)
    _git(repo_b, "checkout", "-b", "feature/repo-b")
    panes = [
        PaneInfo(session="s2", window="0", pane="0", pane_id="%20", target="s2:0.0", current_path=str(repo_a), command="codex", active=True, window_active=False, title="", pid=20, window_name="codex"),
        PaneInfo(session="s2", window="1", pane="0", pane_id="%21", target="s2:1.0", current_path=str(repo_b), command="claude", active=True, window_active=True, title="", pid=21, window_name="claude"),
    ]
    info = SessionInfo(session="s2", panes=panes, selected_pane=panes[1], agents=[])

    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False)
    rows = {row["window"]: row for row in payload["window_metadata"]}

    assert rows["0"]["path"] == str(repo_a)
    assert "git" not in rows["0"]
    assert rows["1"]["path"] == str(repo_b)
    assert "git" not in rows["1"]


def test_activity_work_summary_prefers_recent_dirty_repo(tmp_path):
    repos = []
    for name in ("old", "recent"):
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "init")
        repos.append(repo)
    (repos[1] / "f.txt").write_text("changed\n", encoding="utf-8")
    os.utime(repos[1] / "f.txt", (2_000_000_000, 2_000_000_000))
    panes = [_pane("s3", 0, repos[0]), _pane("s3", 1, repos[1])]
    info = SessionInfo(session="s3", panes=panes, selected_pane=panes[0], agents=[])

    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))

    assert work["git"]["root"] == str(repos[1].resolve())
    assert work["repos"][0]["root"] == str(repos[1].resolve())


def test_activity_work_summary_prefers_current_branch_pr_within_same_signal(monkeypatch, tmp_path):
    config_repo = tmp_path / "ai-config"
    work_repo = tmp_path / "dynamo4"
    _init_repo(config_repo)
    _init_repo(work_repo)
    _git(work_repo, "remote", "add", "origin", "git@github.com:ai-dynamo/dynamo.git")
    _git(work_repo, "checkout", "-b", "keivenchang/DIS-2228__qwen3-coder-tool-calls-v2")
    _git(work_repo, "update-ref", "refs/remotes/origin/pull-request/10853", "HEAD")
    (config_repo / "f.txt").write_text("newer config edit\n", encoding="utf-8")
    os.utime(config_repo / "f.txt", (2_000_000_000, 2_000_000_000))
    panes = [_pane("4", 0, config_repo)]
    info = SessionInfo(session="4", panes=panes, selected_pane=panes[0], agents=[])

    monkeypatch.setattr(
        metadata,
        "candidate_session_cwd_entries",
        lambda _info: [(str(config_repo), 0), (str(work_repo), 0)],
    )

    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))

    assert work["git"]["root"] == str(work_repo.resolve())
    assert work["repos"][0]["root"] == str(work_repo.resolve())
    assert work["pull_request"]["number"] == 10853


def test_activity_work_summary_prefers_actor_detached_pr_over_memory_edit(monkeypatch, tmp_path):
    memory_repo = tmp_path / "ai-config"
    pr_repo = tmp_path / "frontend-crates"
    _init_repo(memory_repo)
    _init_repo(pr_repo)
    _git(pr_repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(pr_repo, "update-ref", "refs/remotes/origin/pull-request/120", "HEAD")
    _git(pr_repo, "checkout", "--detach", "HEAD")

    memory_file = memory_repo / "claude" / "memory" / "-home-user" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("remembered\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(memory_file)}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )

    home_like = tmp_path / "home"
    home_like.mkdir()
    monkeypatch.setattr(metadata, "session_workdir", lambda session: home_like)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    monkeypatch.setattr(metadata, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})

    pane = _pane("120", 0, home_like)
    agent = AgentInfo(
        session="120", kind="claude", pid=1, pane_target="120:0.0", command="claude",
        cwd=str(pr_repo), status="running", session_id=None, transcript=str(transcript), error=None,
    )
    info = SessionInfo(session="120", panes=[pane], selected_pane=pane, agents=[agent])

    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))

    assert work["git"]["root"] == str(pr_repo.resolve())
    assert work["pull_request"]["number"] == 120
    roots = {entry["root"] for entry in work["repos"]}
    assert str(memory_repo.resolve()) in roots


def test_activity_work_summary_prefers_detached_pr_over_later_actor_memory_cwd(monkeypatch, tmp_path):
    memory_repo = tmp_path / "ai-config"
    pr_repo = tmp_path / "frontend-crates"
    _init_repo(memory_repo)
    _init_repo(pr_repo)
    _git(pr_repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(pr_repo, "update-ref", "refs/remotes/origin/pull-request/120", "HEAD")
    _git(pr_repo, "checkout", "--detach", "HEAD")

    memory_file = memory_repo / "claude" / "memory" / "-home-user" / "reference_slack_users_mapping.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("remembered\n", encoding="utf-8")
    pr_file = pr_repo / "parsers" / "v1" / "tests" / "scratch_inkling_review.rs"
    pr_file.parent.mkdir(parents=True)
    pr_file.write_text("review scratch\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": str(pr_file)}},
                ]},
            }),
            json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": str(memory_file)}},
                ]},
            }),
        ]) + "\n",
        encoding="utf-8",
    )

    home_like = tmp_path / "home"
    home_like.mkdir()
    monkeypatch.setattr(metadata, "session_workdir", lambda session: home_like)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    monkeypatch.setattr(metadata, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})

    pane = _pane("120", 0, home_like)
    agent = AgentInfo(
        session="120", kind="claude", pid=1, pane_target="120:0.0", command="claude",
        cwd=str(memory_file.parent), status="running", session_id=None, transcript=str(transcript), error=None,
    )
    info = SessionInfo(session="120", panes=[pane], selected_pane=pane, agents=[agent])

    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))

    assert work["git"]["root"] == str(pr_repo.resolve())
    assert work["pull_request"]["number"] == 120
    assert [entry["root"] for entry in work["repos"]][:2] == [str(pr_repo.resolve()), str(memory_repo.resolve())]


def test_candidate_session_cwds_surfaces_transcript_touched_repo(monkeypatch, tmp_path):
    # A claude launched from a NON-repo cwd (e.g. $HOME) that edits files in a real repo must still
    # surface that repo: the transcript-touched dir is fed into candidate_session_cwds, so repo
    # detection finds it even though the live pane cwd has no git checkout. This is the "8003 says
    # 'no git checkout detected' while actually working in ~/yolomux.dev8003" bug.
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "f.py")
    _git(repo, "commit", "-m", "init")
    repo_root = str(repo.resolve())

    home_like = tmp_path / "home"   # a non-repo dir standing in for $HOME
    home_like.mkdir()

    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(repo / "f.py")}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )

    # isolate the session-number fallbacks so only the live cwd + transcript signal matter
    monkeypatch.setattr(metadata, "session_workdir", lambda session: home_like)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)

    pane = _pane("8003", 0, home_like)
    agent = AgentInfo(
        session="8003", kind="claude", pid=1, pane_target="8003:0.0", command="claude",
        cwd=str(home_like), status="running", session_id=None, transcript=str(transcript), error=None,
    )
    info = SessionInfo(session="8003", panes=[pane], selected_pane=pane, agents=[agent])

    candidates = metadata.candidate_session_cwds(info)
    assert repo_root in candidates, "the transcript-touched repo dir is a candidate cwd"

    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))
    assert work["git"]["root"] == repo_root, "repo detected from the transcript even though the pane cwd is a non-repo"
    roots = {entry["root"] for entry in work["repos"]}
    assert repo_root in roots


def test_activity_work_summary_prefers_transcript_edit_over_recent_live_repo(monkeypatch, tmp_path):
    edited_repo = tmp_path / "edited"
    live_repo = tmp_path / "live"
    _init_repo(edited_repo)
    _init_repo(live_repo)
    edited_root = str(edited_repo.resolve())
    live_root = str(live_repo.resolve())
    (live_repo / "f.txt").write_text("newer live edit\n", encoding="utf-8")
    os.utime(live_repo / "f.txt", (2_000_000_000, 2_000_000_000))

    home_like = tmp_path / "home"
    home_like.mkdir()
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(edited_repo / "f.txt")}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(metadata, "session_workdir", lambda session: home_like)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    monkeypatch.setattr(metadata, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})

    pane = _pane("8002", 0, live_repo)
    agent = AgentInfo(
        session="8002", kind="claude", pid=1, pane_target="8002:0.0", command="claude",
        cwd=str(home_like), status="running", session_id=None, transcript=str(transcript), error=None,
    )
    info = SessionInfo(session="8002", panes=[pane], selected_pane=pane, agents=[agent])

    candidates = metadata.candidate_session_cwds(info)
    assert candidates.index(edited_root) < candidates.index(live_root)

    work = metadata.activity_work_summary_from_graph(metadata.session_work_graph(info, MetadataCache(), allow_network=False))
    assert work["git"]["root"] == edited_root
    assert work["repos"][0]["root"] == edited_root
    assert work["repos"][1]["root"] == live_root


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

    assert current_branch_pull_request(git_data, MetadataCache(), allow_network=False) is None


def test_feature_branch_can_use_head_subject_pr_hint():
    git_data = {
        "github_repo": REPO,
        "branch": "keivenc/example",
        "head": "abc1234567 fix(parser): parse dangling reasoning end markers (#9981)",
        "head_sha": "abc123456789",
    }

    pr = current_branch_pull_request(git_data, MetadataCache(), allow_network=False)

    assert pr is not None
    assert pr["number"] == 9981
    assert pr["source"] == "head-subject"


def test_git_inventory_uses_github_upstream_when_origin_is_local(monkeypatch, tmp_path):
    # Linked worktrees often use a local origin and the real GitHub repository as upstream; PR lookup
    # must still resolve the current branch by head branch instead of dropping all GitHub metadata.
    repo = tmp_path / "frontend-crates2"
    _init_repo(repo)
    branch = "keivenchang/DIS-2267__dsv4-drop-truncated-calls"
    _git(repo, "checkout", "-b", branch)
    (repo / "fix.rs").write_text("fix\n", encoding="utf-8")
    _git(repo, "add", "fix.rs")
    _git(repo, "commit", "-m", "fix(parsers-v2): drop DSv4 tool calls truncated mid-call to match v1 batch")
    local_origin = tmp_path / "frontend-crates"
    local_origin.mkdir()
    _git(repo, "remote", "add", "origin", str(local_origin))
    _git(repo, "remote", "add", "upstream", "git@github.com:ai-dynamo/frontend-crates.git")

    queries = []

    def fake_by_branch(github_repo, queried_branch, cache, allow_network=True):
        queries.append((github_repo, queried_branch, allow_network))
        return {"number": 79, "state": "open", "source": "github-api", "title": "fix(parsers-v2): drop DSv4 calls"}

    monkeypatch.setattr(metadata, "github_pull_request_by_branch", fake_by_branch)

    git_data = metadata.git_inventory(str(repo))
    pr = metadata.current_branch_pull_request(git_data, MetadataCache(), allow_network=True)

    assert git_data["github_repo"] == {
        "owner": "ai-dynamo",
        "name": "frontend-crates",
        "url": "https://github.com/ai-dynamo/frontend-crates",
    }
    assert pr["number"] == 79
    assert queries == [(git_data["github_repo"], branch, True)]


def test_metadata_public_helpers_remain_available_after_client_split():
    assert github_checks_unknown()["state"] == "unknown"
    assert extract_linear_ids("OPS-123 is linked to INFRA-44 and OPS-123") == ["OPS-123", "INFRA-44"]


def test_session_to_json_includes_latest_agent_transcript_mtime(tmp_path):
    older = tmp_path / "older.jsonl"
    newer = tmp_path / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    older_mtime = 1_770_000_000
    newer_mtime = 1_780_000_000
    older.touch()
    newer.touch()
    os.utime(older, (older_mtime, older_mtime))
    os.utime(newer, (newer_mtime, newer_mtime))
    agents = [
        AgentInfo("1", "codex", 1, "1:0.0", "codex", None, "running", "old", str(older), None),
        AgentInfo("1", "claude", 2, "1:0.1", "claude", None, "running", "new", str(newer), None),
        AgentInfo("1", "codex", 3, "1:0.2", "codex", None, "running", "missing", str(tmp_path / "missing.jsonl"), None),
    ]
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=agents)

    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False)

    assert payload["transcript_mtime"] == newer_mtime


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
    assert issue["url"] == "https://linear.app/issue/OPS-123"
    assert issue["url"].endswith("/OPS-123")


def test_linear_issue_url_uses_env_base(monkeypatch):
    monkeypatch.setenv("YOLOMUX_LINEAR_ISSUE_BASE_URL", "https://linear.app/dynamo/issue/")

    assert linear_client.linear_issue_url("OPS-123") == "https://linear.app/dynamo/issue/OPS-123"


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


def test_github_graphql_posts_query_with_token(monkeypatch):
    calls = []

    def fake_http_json(url, headers, timeout, payload=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(github_client, "github_token", lambda: "test-token")
    monkeypatch.setattr(github_client, "http_json", fake_http_json)

    result = github_client.github_graphql("query($id:Int!){x}", {"id": 7})

    assert result == {"ok": True}
    assert len(calls) == 1
    call = calls[0]
    assert call["url"].endswith("/graphql")
    assert call["headers"]["Accept"] == "application/vnd.github+json"
    assert call["headers"]["User-Agent"] == "YOLOmux"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Authorization"] == "token test-token"
    assert call["payload"] == {"query": "query($id:Int!){x}", "variables": {"id": 7}}


def test_linear_issue_from_api_posts_query_with_token(monkeypatch):
    calls = []

    def fake_http_json(url, headers, timeout, payload=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout, "payload": payload})
        return {
            "data": {
                "issue": {
                    "identifier": "OPS-123",
                    "title": "Fix tabs",
                    "url": "https://linear.app/dynamo/issue/OPS-123",
                    "state": {"name": "Done"},
                }
            }
        }

    monkeypatch.setattr(linear_client, "linear_key", lambda: "linear-token")
    monkeypatch.setattr(linear_client, "http_json", fake_http_json)

    issue = linear_client.linear_issue_from_api("OPS-123")

    assert issue == {
        "identifier": "OPS-123",
        "title": "Fix tabs",
        "state": "Done",
        "url": "https://linear.app/dynamo/issue/OPS-123",
        "source": "linear-api",
    }
    assert len(calls) == 1
    call = calls[0]
    assert call["headers"] == {"Authorization": "linear-token", "Content-Type": "application/json"}
    assert call["payload"]["variables"] == {"id": "OPS-123"}
    assert "issue(id: $id)" in call["payload"]["query"]


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
    # a None result (transient 403/429/5xx/timeout, or genuinely no PR) is cached only
    # briefly so it retries soon, while a real value keeps the full positive TTL.
    cache = MetadataCache(ttl_seconds=300)
    github_client.cached_metadata(cache, "real", True, lambda: {"x": 1})
    github_client.cached_metadata(cache, "none", True, lambda: None)
    real_expiry = cache.values["real"][0]
    none_expiry = cache.values["none"][0]
    assert cache.values["none"][1] is None
    assert real_expiry - none_expiry > 60


def test_tail_file_lines_reads_a_bounded_window_from_eof(tmp_path):
    # return the last N lines without scanning the whole file.
    big = tmp_path / "transcript.jsonl"
    big.write_text("".join(f"row-{i}\n" for i in range(5000)))
    assert tail_file_lines(big, 3).splitlines() == ["row-4997", "row-4998", "row-4999"]
    small = tmp_path / "small.txt"
    small.write_text("a\nb\n")
    assert tail_file_lines(small, 10) == "a\nb\n"


def test_metadata_cache_is_bounded_and_sweeps_expired_on_write():
    # the cache stays bounded (cap + sweep expired) so dead branch/sha keys don't leak for
    # the process lifetime.
    # MetadataCache is now a TtlCache subclass; max_entries is the (inherited) cap.
    cache = MetadataCache(ttl_seconds=300)
    cap = cache.max_entries
    for i in range(cap + 1):
        cache.set(f"expired{i}", i, ttl=0)  # immediately expired
    cache.set("fresh", 1)
    assert len(cache.values) <= cap
    assert "fresh" in cache.values
    # a large burst of live entries is also capped.
    cache2 = MetadataCache(ttl_seconds=300)
    for i in range(cap + 100):
        cache2.set(f"k{i}", i)
    assert len(cache2.values) <= cap


def test_parse_pull_request_ref_accepts_short_and_url_forms():
    # owner/repo#N, owner/repo/N, and full PR URLs normalize to the canonical owner/repo#N.
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
    # non-github hosts, missing/zero PR numbers, repo-only refs, and issue URLs are rejected.
    for text in ("https://gitlab.com/owner/repo/pull/7", "owner/repo", "owner/repo#0",
                 "not a ref", "https://github.com/owner/repo/issues/3", "", None):
        assert parse_pull_request_ref(text) is None, text


def test_watched_pr_metadata_dedupes_caps_and_flags_invalid():
    # dedupe by canonical ref, cap at WATCHED_PR_LIMIT, collect invalid entries, and (offline)
    # return the fallback PR shape carrying {ref, url}.
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
    # the focused pane's live cwd (after `cd`) outranks the session-number default workdir, so
    # the project follows the pane instead of staying pinned to dynamoN.
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
    default_dir = tmp_path / "dynamo1"
    default_dir.mkdir()
    monkeypatch.setattr(metadata, "session_workdir", lambda session: default_dir)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    assert metadata.candidate_session_cwds(info) == [str(default_dir.resolve())]


def test_git_worktree_identity_names_linked_worktree_vs_parent(tmp_path):
    # S7: a linked worktree resolves to its parent repo; the main checkout returns None.
    def run(*args, cwd):
        subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)

    main = tmp_path / "main"
    main.mkdir()
    run("git", "init", "-q", cwd=main)
    run("git", "config", "user.email", "t@example.com", cwd=main)
    run("git", "config", "user.name", "T", cwd=main)
    (main / "f.txt").write_text("hi\n", encoding="utf-8")
    run("git", "add", "f.txt", cwd=main)
    run("git", "commit", "-qm", "init", cwd=main)
    wt = tmp_path / "wt"
    run("git", "worktree", "add", "-q", str(wt), "-b", "feature", cwd=main)

    assert metadata.git_worktree_identity(str(main), str(main)) is None
    ident = metadata.git_worktree_identity(str(wt), str(wt))
    assert ident is not None
    assert Path(ident["parent_root"]).name == "main"
    assert ident["name"] == "wt"
    assert ident["path"] == str(wt)


def test_linked_worktrees_share_complete_local_branch_inventory(tmp_path):
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "T")
    (main / "f.txt").write_text("hi\n", encoding="utf-8")
    _git(main, "add", "f.txt")
    _git(main, "commit", "-m", "init")
    _git(main, "branch", "spare")
    _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

    main_inventory = metadata.git_inventory(str(main))
    wt_inventory = metadata.git_inventory(str(wt))
    assert main_inventory is not None and wt_inventory is not None

    main_branches = {branch["name"]: branch for branch in main_inventory["other_branches"]["branches"]}
    wt_branches = {branch["name"]: branch for branch in wt_inventory["other_branches"]["branches"]}

    assert set(main_branches) == {"main", "spare", "feature"}
    assert set(wt_branches) == {"main", "spare", "feature"}
    assert main_branches["main"]["current"] is True
    assert all(main_branches[name]["current"] is False for name in {"spare", "feature"})
    assert wt_branches["feature"]["current"] is True
    assert all(wt_branches[name]["current"] is False for name in {"main", "spare"})
    assert main_inventory["local_repository"]["id"] == wt_inventory["local_repository"]["id"]
    assert main_inventory["local_repository"]["worktree_id"] != wt_inventory["local_repository"]["worktree_id"]


def test_session_work_graph_dedupes_shared_worktree_and_preserves_actor_observations(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "feature/shared")
    pane = _pane("s-graph", 0, repo)
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    agents = [
        AgentInfo("s-graph", "claude", 101, pane.target, "claude", str(repo), "running", "a", str(transcript), None),
        AgentInfo("s-graph", "codex", 102, pane.target, "codex", str(repo), "running", "b", str(transcript), None),
    ]
    info = SessionInfo(session="s-graph", panes=[pane], selected_pane=pane, agents=agents)
    monkeypatch.setattr(
        metadata,
        "scan_agent_changes",
        lambda agent: {str(repo / ("claude.txt" if agent.kind == "claude" else "codex.txt")): {"M"}},
    )

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    assert graph["version"] == metadata.WORK_GRAPH_VERSION
    assert len(graph["runtime_actors"]) == 2
    assert len(graph["git_worktrees"]) == 1
    assert len(graph["local_repositories"]) == 1
    assert len(graph["path_observations"]) >= 4
    worktree = next(iter(graph["git_worktrees"].values()))
    local_repository = graph["local_repositories"][worktree["local_repository_id"]]
    assert worktree["local_repository_id"] == local_repository["id"]
    assert {graph["local_branches"][branch_id]["name"] for branch_id in local_repository["local_branch_ids"]} == {"master", "feature/shared"}
    edit_actors = {
        observation["runtime_actor_id"]
        for observation in graph["path_observations"].values()
        if observation["source"] == "edit"
    }
    assert edit_actors == set(graph["runtime_actors"])
    metadata.validate_work_graph(graph)


def test_session_work_graph_shared_worktree_keeps_claude_and_shell_observations_and_one_pr(tmp_path, monkeypatch):
    repo = tmp_path / "frontend-crates3"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(repo, "checkout", "-b", "feature/session-3")
    claude_pane = _pane("3", 0, repo)
    claude_pane = TmuxPaneInfo(**{**claude_pane.__dict__, "window_name": "claude", "command": "claude"})
    shell_pane = _pane("3", 1, repo)
    shell_pane = TmuxPaneInfo(**{**shell_pane.__dict__, "window": "1", "window_name": "bash", "command": "bash", "active": False, "window_active": False})
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    claude = AgentInfo("3", "claude", 101, claude_pane.target, "claude", str(repo), "running", "claude-session", str(transcript), None)
    info = SessionInfo(session="3", panes=[claude_pane, shell_pane], selected_pane=claude_pane, agents=[claude])
    changed_paths = {
        str(repo / "src" / "parser.py"): {"M"},
        str(repo / "tests" / "parser_test.py"): {"M"},
    }
    monkeypatch.setattr(metadata, "scan_agent_changes", lambda _agent: changed_paths)
    monkeypatch.setattr(
        metadata,
        "github_pull_requests_by_branch",
        lambda _repo, branch, _cache, allow_network=True: [
            {"number": 80, "state": "open", "draft": True, "title": "frontend PR", "linear_ids": []}
        ] if branch == "feature/session-3" else [],
    )
    monkeypatch.setattr(metadata, "github_pull_request_by_branch", lambda _repo, branch, cache, allow_network=True: (
        {"number": 80, "state": "open", "draft": True, "title": "frontend PR", "linear_ids": []}
        if branch == "feature/session-3" else None
    ))

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=True)

    assert len(graph["git_worktrees"]) == 1
    assert len(graph["local_repositories"]) == 1
    assert len(graph["pull_requests"]) == 1
    worktree = next(iter(graph["git_worktrees"].values()))
    repository = graph["local_repositories"][worktree["local_repository_id"]]
    assert {graph["local_branches"][branch_id]["name"] for branch_id in repository["local_branch_ids"]} == {"master", "feature/session-3"}
    actors_by_kind = {actor["kind"]: actor for actor in graph["runtime_actors"].values()}
    assert {"claude", "shell"}.issubset(actors_by_kind)
    claude_observations = [
        observation for observation in graph["path_observations"].values()
        if observation["runtime_actor_id"] == actors_by_kind["claude"]["id"]
    ]
    shell_observations = [
        observation for observation in graph["path_observations"].values()
        if observation["tmux_pane_id"] == f"tmux-pane:3:1:1" and observation["runtime_actor_id"] is None
    ]
    assert {observation["path"] for observation in claude_observations}.issuperset(changed_paths)
    assert {observation["path"] for observation in shell_observations} == {str(repo.resolve())}
    assert {pr["number"] for pr in graph["pull_requests"].values()} == {80}
    metadata.validate_work_graph(graph)


def test_session_work_graph_linked_worktrees_share_branch_inventory_but_keep_current_state(tmp_path):
    main = tmp_path / "frontend-crates"
    worktree3 = tmp_path / "frontend-crates3"
    worktree4 = tmp_path / "frontend-crates4"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "T")
    (main / "f.txt").write_text("initial\n", encoding="utf-8")
    _git(main, "add", "f.txt")
    _git(main, "commit", "-m", "init")
    _git(main, "branch", "available")
    _git(main, "worktree", "add", "-q", str(worktree3), "-b", "feature/three")
    _git(main, "worktree", "add", "-q", str(worktree4), "-b", "feature/four")
    (worktree3 / "f.txt").write_text("three dirty\n", encoding="utf-8")
    (worktree4 / "f.txt").write_text("four dirty\n", encoding="utf-8")
    panes = [_pane("linked", 0, main), _pane("linked", 1, worktree3), _pane("linked", 2, worktree4)]
    info = SessionInfo(session="linked", panes=panes, selected_pane=panes[1], agents=[])

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    assert len(graph["git_worktrees"]) == 3
    assert len(graph["local_repositories"]) == 1
    repository = next(iter(graph["local_repositories"].values()))
    assert {graph["local_branches"][branch_id]["name"] for branch_id in repository["local_branch_ids"]} == {"main", "available", "feature/three", "feature/four"}
    current_by_root = {
        worktree["root"]: graph["local_branches"][worktree["current_branch_id"]]["name"]
        for worktree in graph["git_worktrees"].values()
    }
    assert current_by_root == {
        str(main.resolve()): "main",
        str(worktree3.resolve()): "feature/three",
        str(worktree4.resolve()): "feature/four",
    }
    dirty_by_root = {worktree["root"]: worktree["git"]["dirty_count"] for worktree in graph["git_worktrees"].values()}
    assert dirty_by_root == {
        str(main.resolve()): 0,
        str(worktree3.resolve()): 1,
        str(worktree4.resolve()): 1,
    }
    metadata.validate_work_graph(graph)


def test_session_work_graph_branch_activity_keeps_available_branch_without_worktree_activity(tmp_path):
    main = tmp_path / "repo"
    worktree1 = tmp_path / "worktree-1"
    worktree2 = tmp_path / "worktree-2"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "T")
    (main / "f.txt").write_text("initial\n", encoding="utf-8")
    _git(main, "add", "f.txt")
    _git(main, "commit", "-m", "init")
    _git(main, "branch", "B3")
    _git(main, "worktree", "add", "-q", str(worktree1), "-b", "B1")
    _git(main, "worktree", "add", "-q", str(worktree2), "-b", "B2")
    panes = [_pane("activity", 0, worktree1), _pane("activity", 1, worktree2)]
    info = SessionInfo(session="activity", panes=panes, selected_pane=panes[0], agents=[])

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    worktrees = {worktree["root"]: worktree for worktree in graph["git_worktrees"].values()}
    repository = next(iter(graph["local_repositories"].values()))
    branches = {
        graph["local_branches"][branch_id]["name"]: graph["local_branches"][branch_id]
        for branch_id in repository["local_branch_ids"]
    }
    activities_by_branch = {
        branch_name: [
            activity
            for activity in graph["worktree_branch_activity"].values()
            if activity["branch_name_snapshot"] == branch_name
        ]
        for branch_name in branches
    }

    assert branches.keys() >= {"main", "B1", "B2", "B3"}
    assert {activity["git_worktree_id"] for activity in activities_by_branch["B1"]} == {worktrees[str(worktree1.resolve())]["id"]}
    assert {activity["git_worktree_id"] for activity in activities_by_branch["B2"]} == {worktrees[str(worktree2.resolve())]["id"]}
    assert activities_by_branch["B3"] == []
    metadata.validate_work_graph(graph)


def test_session_to_json_emits_only_work_graph_for_git_metadata(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature/projected")
    pane = _pane("s-projection", 0, repo)
    info = SessionInfo(session="s-projection", panes=[pane], selected_pane=pane, agents=[])

    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False)

    assert payload["work_graph"]["version"] == metadata.WORK_GRAPH_VERSION
    assert "project" not in payload
    assert "git" not in payload["window_metadata"][0]
    worktree = next(worktree for worktree in payload["work_graph"]["git_worktrees"].values() if worktree["root"] == str(repo.resolve()))
    local_repository = payload["work_graph"]["local_repositories"][worktree["local_repository_id"]]
    assert local_repository["local_branch_ids"]


def test_session_to_json_builds_one_canonical_graph_without_legacy_projection(tmp_path, monkeypatch):
    repo = tmp_path / "frontend-crates3"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(repo, "checkout", "-b", "feature/session-3")
    pane = TmuxPaneInfo(
        session="3", window="0", pane="0", pane_id="%0", target="3:0.0", current_path=str(repo),
        command="claude", active=True, window_active=True, title="", pid=10, window_name="claude",
    )
    info = SessionInfo(session="3", panes=[pane], selected_pane=pane, agents=[])
    home_like = tmp_path / "home"
    home_like.mkdir()
    original_graph = metadata.session_work_graph
    original_inventory = metadata.git_inventory
    graph_builds = []
    inventory_calls = []

    def counted_inventory(path, **kwargs):
        inventory_calls.append((str(path), dict(kwargs)))
        return original_inventory(path, **kwargs)

    def counted_graph(*args, **kwargs):
        graph = original_graph(*args, **kwargs)
        graph_builds.append(graph)
        return graph

    monkeypatch.setattr(metadata, "session_workdir", lambda session: home_like)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    monkeypatch.setattr(metadata, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})
    monkeypatch.setattr(metadata, "git_inventory", counted_inventory)
    monkeypatch.setattr(metadata, "session_work_graph", counted_graph)
    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False)

    assert len(graph_builds) == 1
    graph = graph_builds[0]
    assert payload["work_graph"] is graph
    worktree = next(item for item in graph["git_worktrees"].values() if item["root"] == str(repo.resolve()))
    local_repository = graph["local_repositories"][worktree["local_repository_id"]]
    graph_branch_names = [graph["local_branches"][branch_id]["name"] for branch_id in local_repository["local_branch_ids"]]
    assert "feature/session-3" in graph_branch_names
    assert "project" not in payload
    assert "git" not in payload["window_metadata"][0]
    assert len(inventory_calls) == 1


def test_session_metadata_retirement_guards_keep_git_ownership_in_work_graph_only(tmp_path):
    repo = tmp_path / "canonical-owner"
    _init_repo(repo)
    pane = _pane("canonical-owner", 0, repo)
    payload = metadata.session_to_json(
        SessionInfo(session="canonical-owner", panes=[pane], selected_pane=pane, agents=[]),
        MetadataCache(),
        allow_network=False,
    )

    assert "project" not in payload
    assert all("git" not in row for row in payload["window_metadata"])
    metadata_source = Path(metadata.__file__).read_text(encoding="utf-8")
    assert "def project_pull_request" not in metadata_source
    frontend_sources = "\n".join(path.read_text(encoding="utf-8") for path in Path("static_src/js/yolomux").glob("*.js"))
    assert "info.project" not in frontend_sources
    assert "window_metadata.git" not in frontend_sources


def test_session_work_graph_keeps_independent_clones_separate_and_hosted_identity_shared(tmp_path, monkeypatch):
    clone_a = tmp_path / "clone-a"
    clone_b = tmp_path / "clone-b"
    _init_repo(clone_a)
    _init_repo(clone_b)
    for repo in (clone_a, clone_b):
        _git(repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(clone_a, "branch", "feature/a")
    _git(clone_b, "branch", "feature/b")
    panes = [_pane("s-clones", 0, clone_a), _pane("s-clones", 1, clone_b)]
    info = SessionInfo(session="s-clones", panes=panes, selected_pane=panes[0], agents=[])
    def fake_prs(_repo, branch, _cache, allow_network=True):
        if branch in {"feature/a", "feature/b"}:
            return [{"number": 80, "state": "open", "title": "shared hosted PR", "linear_ids": []}]
        return []

    monkeypatch.setattr(metadata, "github_pull_requests_by_branch", fake_prs)
    monkeypatch.setattr(metadata, "github_pull_request_by_branch", lambda *_args, **_kwargs: None)

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=True)

    assert len(graph["git_worktrees"]) == 2
    assert len(graph["local_repositories"]) == 2
    assert len(graph["hosted_repositories"]) == 1
    hosted = next(iter(graph["hosted_repositories"].values()))
    assert len(hosted["local_repository_ids"]) == 2
    branch_sets = [
        {graph["local_branches"][branch_id]["name"] for branch_id in repository["local_branch_ids"]}
        for repository in graph["local_repositories"].values()
    ]
    assert {"master", "feature/a"} in branch_sets
    assert {"master", "feature/b"} in branch_sets
    shared_pr_ids = {
        pr_id
        for branch in graph["local_branches"].values()
        if branch["name"] in {"feature/a", "feature/b"}
        for pr_id in branch["pull_request_ids"]
    }
    assert shared_pr_ids == {"pull-request:hosted-repository:github.com/ai-dynamo/frontend-crates:80"}
    assert len(graph["pull_requests"]) == 1
    assert graph["pull_requests"][next(iter(shared_pr_ids))]["local_branch_ids"]


def test_session_work_graph_keeps_missing_historical_edit_observation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    pane = _pane("s-missing", 0, repo)
    agent = AgentInfo("s-missing", "codex", 101, pane.target, "codex", str(repo), "running", "a", None, None)
    info = SessionInfo(session="s-missing", panes=[pane], selected_pane=pane, agents=[agent])
    missing_path = repo / "removed" / "history.txt"
    monkeypatch.setattr(metadata, "scan_agent_changes", lambda _agent: {str(missing_path): {"D"}})

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    observation = next(item for item in graph["path_observations"].values() if item["path_snapshot"] == str(missing_path))
    assert observation["source"] == "edit"
    assert observation["exists"] is False
    assert observation["runtime_actor_id"] in graph["runtime_actors"]
    assert observation["git_worktree_id"] is not None


def test_resolve_path_observation_uses_nearest_nested_repository(tmp_path):
    parent = tmp_path / "parent"
    nested = parent / "nested"
    _init_repo(parent)
    _init_repo(nested)
    resolved = metadata.resolve_path_observation(str(nested / "f.txt"))

    assert resolved["git_root"] == str(nested.resolve())
    assert resolved["git_worktree_id"] == f"git-worktree:{nested.resolve()}"
    assert resolved["local_repository_id"] == f"local-git:{nested.resolve() / '.git'}"


def test_session_work_graph_generation_is_monotonic(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    pane = _pane("s-generation", 0, repo)
    info = SessionInfo(session="s-generation", panes=[pane], selected_pane=pane, agents=[])
    first = metadata.session_work_graph(info, MetadataCache(), allow_network=False)
    second = metadata.session_work_graph(info, MetadataCache(), allow_network=False)
    assert second["generation"] > first["generation"]


def test_session_work_graph_retains_worktree_branch_history_after_switch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature/first")
    pane = _pane("s-history", 0, repo)
    info = SessionInfo(session="s-history", panes=[pane], selected_pane=pane, agents=[])
    first = metadata.session_work_graph(info, MetadataCache(), allow_network=False)
    _git(repo, "checkout", "-b", "feature/second")
    second = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    worktree = next(iter(second["git_worktrees"].values()))
    branches = {branch["name"]: branch for branch in second["local_branches"].values()}
    assert second["local_branches"][worktree["current_branch_id"]]["name"] == "feature/second"
    assert "feature/first" in branches
    historical_activity = [
        activity
        for activity in second["worktree_branch_activity"].values()
        if second["local_branches"][activity["local_branch_id"]]["name"] == "feature/first"
    ]
    assert historical_activity and historical_activity[0]["current"] is False
    assert first["generation"] < second["generation"]


def test_session_work_graph_preserves_deleted_branch_activity_snapshot_and_sha(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature/renamed-away")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature activity")
    feature_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = _pane("s-history-deleted", 0, repo)
    info = SessionInfo(session="s-history-deleted", panes=[pane], selected_pane=pane, agents=[])
    metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    _git(repo, "checkout", "master")
    _git(repo, "branch", "-D", "feature/renamed-away")
    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    worktree = next(iter(graph["git_worktrees"].values()))
    activities = [
        graph["worktree_branch_activity"][activity_id]
        for activity_id in worktree["branch_activity_ids"]
    ]
    historical = next(activity for activity in activities if activity["branch_name_snapshot"] == "feature/renamed-away")
    historical_branch = graph["local_branches"][historical["local_branch_id"]]
    assert historical["current"] is False
    assert historical["observed_head_sha"] == feature_sha
    assert historical_branch["missing"] is True
    assert historical_branch["head_sha"] == feature_sha
    current = [activity for activity in activities if activity["current"]]
    assert len(current) == 1
    assert graph["local_branches"][current[0]["local_branch_id"]]["name"] == "master"


def test_session_work_graph_keeps_detached_head_branchless_and_unborn_branch_with_null_sha(tmp_path):
    detached = tmp_path / "detached"
    _init_repo(detached)
    detached_sha = _git(detached, "rev-parse", "HEAD").stdout.strip()
    _git(detached, "checkout", "--detach")
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-b", "main")
    _git(unborn, "config", "user.email", "t@example.com")
    _git(unborn, "config", "user.name", "T")
    panes = [_pane("s-detached-unborn", 0, detached), _pane("s-detached-unborn", 1, unborn)]
    info = SessionInfo(session="s-detached-unborn", panes=panes, selected_pane=panes[0], agents=[])

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)
    worktrees = {worktree["root"]: worktree for worktree in graph["git_worktrees"].values()}
    branches_by_repository = {
        repository_id: [graph["local_branches"][branch_id] for branch_id in repository["local_branch_ids"]]
        for repository_id, repository in graph["local_repositories"].items()
    }
    detached_worktree = worktrees[str(detached.resolve())]
    unborn_worktree = worktrees[str(unborn.resolve())]

    assert detached_worktree["current_branch_id"] is None
    assert detached_worktree["git"]["head_sha"] == detached_sha
    assert branches_by_repository[detached_worktree["local_repository_id"]]
    assert all(branch["name"] != "HEAD" for branch in branches_by_repository[detached_worktree["local_repository_id"]])
    unborn_branch = graph["local_branches"][unborn_worktree["current_branch_id"]]
    assert unborn_branch["name"] == "main"
    assert unborn_branch["unborn"] is True
    assert unborn_branch["head_sha"] is None
    metadata.validate_work_graph(graph)


def test_session_work_graph_associates_current_and_other_branch_prs_once(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(repo, "checkout", "-b", "feature/current")
    _git(repo, "branch", "feature/other")
    pane = _pane("s-prs", 0, repo)
    info = SessionInfo(session="s-prs", panes=[pane], selected_pane=pane, agents=[])

    def fake_pr(_repo, branch, _cache, allow_network=True):
        number = {"feature/current": 80, "feature/other": 81}.get(branch)
        return {"number": number, "title": branch, "description": "", "linear_ids": []} if number else None

    monkeypatch.setattr(metadata, "github_pull_request_by_branch", fake_pr)
    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=False)

    assert {pr["number"] for pr in graph["pull_requests"].values()} == {80, 81}
    by_name = {branch["name"]: branch for branch in graph["local_branches"].values()}
    assert len(by_name["feature/current"]["pull_request_ids"]) == 1
    assert len(by_name["feature/other"]["pull_request_ids"]) == 1


def test_session_work_graph_pr_lookup_states_distinguish_not_requested_and_none(tmp_path, monkeypatch):
    local_repo = tmp_path / "local"
    hosted_repo = tmp_path / "hosted"
    _init_repo(local_repo)
    _init_repo(hosted_repo)
    _git(hosted_repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(hosted_repo, "checkout", "-b", "feature/no-pr")
    panes = [_pane("s-pr-state", 0, local_repo), _pane("s-pr-state", 1, hosted_repo)]
    info = SessionInfo(session="s-pr-state", panes=panes, selected_pane=panes[0], agents=[])
    monkeypatch.setattr(metadata, "github_pull_request_by_branch", lambda *_args, **_kwargs: None)

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=True)
    branches = {branch["name"]: branch for branch in graph["local_branches"].values()}

    assert branches["feature/no-pr"]["pull_request_lookup_state"] == "none"
    local_branch = next(branch for branch in graph["local_branches"].values() if branch["local_repository_id"] != branches["feature/no-pr"]["local_repository_id"])
    assert local_branch["pull_request_lookup_state"] == "not-requested"


def test_session_work_graph_keeps_non_git_paths_and_labels_local_and_failed_pr_lookups(tmp_path, monkeypatch):
    non_git = tmp_path / "notes"
    non_git.mkdir()
    (non_git / "readme.txt").write_text("not a repository\n", encoding="utf-8")
    local_repo = tmp_path / "local"
    hosted_repo = tmp_path / "hosted"
    _init_repo(local_repo)
    _init_repo(hosted_repo)
    _git(hosted_repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(hosted_repo, "branch", "feature/failing-lookup")
    panes = [
        _pane("s-no-git", 0, non_git),
        _pane("s-no-git", 1, local_repo),
        _pane("s-no-git", 2, hosted_repo),
    ]
    info = SessionInfo(session="s-no-git", panes=panes, selected_pane=panes[0], agents=[])
    monkeypatch.setattr(metadata, "github_pull_requests_by_branch", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(metadata, "github_pull_request_by_branch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(metadata, "github_pull_request_branch_lookup_state", lambda *_args, **_kwargs: "error")

    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=True)
    observations = {observation["path"]: observation for observation in graph["path_observations"].values()}
    local_worktree = next(worktree for worktree in graph["git_worktrees"].values() if worktree["root"] == str(local_repo.resolve()))
    hosted_worktree = next(worktree for worktree in graph["git_worktrees"].values() if worktree["root"] == str(hosted_repo.resolve()))
    local_branch = graph["local_branches"][local_worktree["current_branch_id"]]
    failed_branch = next(branch for branch in graph["local_branches"].values() if branch["name"] == "feature/failing-lookup")

    assert observations[str(non_git.resolve())]["git_worktree_id"] is None
    assert local_branch["pull_request_ids"] == []
    assert local_branch["pull_request_lookup_state"] == "not-requested"
    assert failed_branch["pull_request_ids"] == []
    assert failed_branch["pull_request_lookup_state"] == "error"
    metadata.validate_work_graph(graph)


def test_live_session_3_frontend_crates_graph_when_available():
    sessions, errors = discover_sessions(["3"])
    if errors or "3" not in sessions:
        pytest.skip("live tmux session 3 is not available in this test environment")
    info = sessions["3"]
    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=True)
    worktree = next(
        (item for item in graph["git_worktrees"].values() if item["root"].endswith("/frontend-crates3")),
        None,
    )
    if worktree is None:
        pytest.skip("live session 3 is not currently associated with frontend-crates3")
    local_repository = graph["local_repositories"][worktree["local_repository_id"]]
    branch_names = {graph["local_branches"][branch_id]["name"] for branch_id in local_repository["local_branch_ids"]}
    actual_names = {
        line.strip().removeprefix("*").strip()
        for line in _git(Path(worktree["root"]), "branch", "--format=%(refname:short)").stdout.splitlines()
        if line.strip()
    }
    assert branch_names == actual_names
    claude_pane = next((pane for pane in info.panes if pane.window == "0" and pane.window_name == "claude"), None)
    assert claude_pane is not None
    assert claude_pane.target in {
        pane["target"]
        for pane in graph["tmux_panes"].values()
        if pane["current_path"] == str(Path(worktree["root"]).resolve())
    }
    hosted = graph["hosted_repositories"][worktree["hosted_repository_id"]]
    assert 80 in {graph["pull_requests"][pr_id]["number"] for pr_id in hosted["pull_request_ids"]}
    dynamo_prs = {
        pr["number"]
        for pr in graph["pull_requests"].values()
        if pr.get("hosted_repository_id") == "hosted-repository:github.com/ai-dynamo/dynamo"
    }
    assert 11251 in dynamo_prs


def test_local_branch_inventory_excludes_remote_only_pr_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "init")
    current_branch = "keivenchang/DIS-2223__current"
    _git(repo, "checkout", "-b", current_branch)
    (repo / "current.txt").write_text("current\n", encoding="utf-8")
    _git(repo, "add", "current.txt")
    _git(repo, "commit", "-m", "current")
    pr_branch = "keivenchang/DIS-2212__cut-over-parsers-to-frontend-crates"
    _git(repo, "checkout", "-b", pr_branch)
    (repo / "parser.txt").write_text("parser\n", encoding="utf-8")
    _git(repo, "add", "parser.txt")
    _git(repo, "commit", "-m", "chore(parsers): cut over lib/parsers to dynamo-parsers crate")
    pr_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", current_branch)
    _git(repo, "branch", "-D", pr_branch)
    _git(repo, "update-ref", f"refs/remotes/origin/{pr_branch}", pr_sha)
    _git(repo, "update-ref", "refs/remotes/origin/pull-request/10423", pr_sha)

    inventory = metadata.local_branch_inventory(str(repo), current_branch)
    branches = {branch["name"]: branch for branch in inventory["branches"]}

    assert current_branch in branches
    assert branches[current_branch]["remote"] is False
    assert pr_branch not in branches


def test_local_branch_inventory_uses_git_branch_default_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "init")

    def commit_branch(branch: str, filename: str, value: str, commit_date: str) -> None:
        _git(repo, "checkout", "-b", branch)
        (repo / filename).write_text(f"{value}\n", encoding="utf-8")
        _git(repo, "add", filename)
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = commit_date
        env["GIT_COMMITTER_DATE"] = commit_date
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", value],
            capture_output=True,
            check=True,
            env=env,
            text=True,
        )

    commit_branch("z-newer", "newer.txt", "newer", "2030-01-02T00:00:00+0000")
    _git(repo, "checkout", "master")
    commit_branch("a-older", "older.txt", "older", "2000-01-02T00:00:00+0000")
    _git(repo, "checkout", "master")

    default_order = _git(repo, "branch", "--format=%(refname:short)").stdout.splitlines()
    committerdate_order = _git(
        repo,
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname:short)",
        "refs/heads",
    ).stdout.splitlines()
    inventory = metadata.local_branch_inventory(str(repo), "master", branch_limit=None)

    assert default_order != committerdate_order
    assert [branch["name"] for branch in inventory["branches"]] == default_order


def test_indexed_repo_summaries_excludes_remote_only_pr_without_local_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "init")
    current_branch = _git(repo, "branch", "--show-current").stdout.strip()
    pr_branch = "keivenchang/DIS-2064__frontend-coverage"
    _git(repo, "checkout", "-b", pr_branch)
    (repo / "frontend.txt").write_text("coverage\n", encoding="utf-8")
    _git(repo, "add", "frontend.txt")
    _git(repo, "commit", "-m", "test(frontend): match tooltip conventions")
    pr_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", current_branch)
    _git(repo, "branch", "-D", pr_branch)
    _git(repo, "update-ref", f"refs/remotes/origin/{pr_branch}", pr_sha)
    _git(repo, "update-ref", "refs/remotes/origin/pull-request/10228", pr_sha)

    summaries = metadata.indexed_repo_summaries([str(repo)], cache=MetadataCache(), allow_network=False)
    branches = {branch["name"]: branch for branch in summaries[0]["other_branches"]["branches"]}

    assert current_branch in branches
    assert branches[current_branch]["remote"] is False
    assert pr_branch not in branches


def test_open_pr_on_other_branch_resolves_by_head_branch(monkeypatch):
    # An OPEN PR on a non-current branch has no local (#N) marker (that only appears after a
    # squash-merge), so enrich_branch_pull_requests must fall back to a by-head-branch lookup —
    # otherwise YO!info shows the branch + Linear ID but never the open PR number (the dynamo2
    # DIS-2212 / PR #10423 bug). The current branch (resolved separately) and main are skipped.
    queried = []

    def fake_by_branch(repo, branch, cache, allow_network=True):
        queried.append(branch)
        if branch == "keivenchang/DIS-2212__cut-over-parsers-to-frontend-crates":
            return {"number": 10423, "state": "open", "source": "github-api", "title": "chore(parsers): cut over"}
        return None

    monkeypatch.setattr(metadata, "github_pull_request_by_branch", fake_by_branch)
    git_data = {
        "github_repo": REPO,
        "other_branches": {
            "branches": [
                {"name": "keivenchang/DIS-2223__current", "current": True, "pull_request": None},
                {"name": "main", "current": False, "pull_request": None},
                {"name": "keivenchang/DIS-2212__cut-over-parsers-to-frontend-crates", "current": False, "pull_request": None},
            ],
        },
    }
    metadata.enrich_branch_pull_requests(git_data, MetadataCache(), allow_network=True)
    branches = {b["name"]: b for b in git_data["other_branches"]["branches"]}
    # the open PR on the other branch is now resolved by head branch
    assert branches["keivenchang/DIS-2212__cut-over-parsers-to-frontend-crates"]["pull_request"]["number"] == 10423
    # the current branch and main are NOT queried by this fallback
    assert "keivenchang/DIS-2223__current" not in queried
    assert "main" not in queried
    assert "keivenchang/DIS-2212__cut-over-parsers-to-frontend-crates" in queried


def test_other_branch_with_local_pr_number_still_uses_by_number(monkeypatch):
    # The existing merged-PR path (local (#N) -> by-number) must keep working and must NOT trigger
    # the by-branch fallback.
    branch_queries = []
    monkeypatch.setattr(metadata, "github_pull_request_by_branch",
                        lambda repo, branch, cache, allow_network=True: branch_queries.append(branch))
    git_data = {
        "github_repo": REPO,
        "other_branches": {
            "branches": [
                {"name": "keivenc/merged", "current": False, "subject": "fix (#9981)",
                 "pull_request": {"number": 9981, "title": "fix"}},
            ],
        },
    }
    metadata.enrich_branch_pull_requests(git_data, MetadataCache(), allow_network=False)
    pr = git_data["other_branches"]["branches"][0]["pull_request"]
    assert pr["number"] == 9981
    assert branch_queries == []  # by-number path used; no by-branch fallback


def test_session_work_graph_keeps_multiple_branch_prs_and_fork_head_identity(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(repo, "checkout", "-b", "feature/current")
    _git(repo, "branch", "feature/reused")
    pane = _pane("s-multi-pr", 0, repo)
    info = SessionInfo(session="s-multi-pr", panes=[pane], selected_pane=pane, agents=[])

    def fake_prs(_repo, branch, _cache, allow_network=True):
        if branch != "feature/reused":
            return []
        return [
            {"number": 90, "state": "closed", "title": "old", "linear_ids": []},
            {
                "number": 91,
                "state": "open",
                "title": "current",
                "linear_ids": [],
                "head_repository": {"full_name": "contributor/frontend-crates", "url": "https://github.com/contributor/frontend-crates"},
                "head_branch_name": "feature/reused",
            },
            {
                "number": 91,
                "state": "open",
                "title": "current duplicate lookup evidence",
                "linear_ids": [],
                "head_repository": {"full_name": "contributor/frontend-crates", "url": "https://github.com/contributor/frontend-crates"},
                "head_branch_name": "feature/reused",
            },
        ]

    monkeypatch.setattr(metadata, "github_pull_requests_by_branch", fake_prs)
    graph = metadata.session_work_graph(info, MetadataCache(), allow_network=True)

    branch = next(branch for branch in graph["local_branches"].values() if branch["name"] == "feature/reused")
    assert branch["pull_request_lookup_state"] == "ready"
    assert {graph["pull_requests"][pr_id]["number"] for pr_id in branch["pull_request_ids"]} == {90, 91}
    assert len(branch["pull_request_ids"]) == 2
    base_hosted = next(hosted for hosted in graph["hosted_repositories"].values() if hosted["owner"] == "ai-dynamo")
    assert len(base_hosted["pull_request_ids"]) == 2
    fork_id = "hosted-repository:github.com/contributor/frontend-crates"
    assert graph["pull_requests"][next(pr_id for pr_id in branch["pull_request_ids"] if graph["pull_requests"][pr_id]["number"] == 91)]["head_hosted_repository_id"] == fork_id
    assert fork_id in graph["hosted_repositories"]


def test_branch_pr_lookup_state_preserves_error_and_stale_evidence(monkeypatch):
    repo = {"owner": "ai-dynamo", "name": "frontend-crates", "url": "https://github.com/ai-dynamo/frontend-crates"}
    monkeypatch.setattr(metadata, "github_pull_requests_by_branch", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(metadata, "github_pull_request_branch_lookup_state", lambda *_args, **_kwargs: "error")
    monkeypatch.setattr(metadata, "github_pull_request_by_branch", lambda *_args, **_kwargs: None)

    no_evidence, no_evidence_state = metadata.branch_pull_requests(repo, "feature/error", MetadataCache(), allow_network=True)
    stale_evidence, stale_state = metadata.branch_pull_requests(
        repo,
        "feature/stale",
        MetadataCache(),
        allow_network=True,
        known=[{"number": 92, "state": "open"}],
    )

    assert no_evidence == []
    assert no_evidence_state == "error"
    assert stale_evidence == [{"number": 92, "state": "open"}]
    assert stale_state == "stale"


def test_graph_keeps_fork_pr_when_no_local_branch_survives(monkeypatch):
    graph = metadata.empty_work_graph()
    hosted_id = "hosted-repository:github.com/ai-dynamo/frontend-crates"
    graph["hosted_repositories"][hosted_id] = {
        "id": hosted_id,
        "provider": "github",
        "owner": "ai-dynamo",
        "name": "frontend-crates",
        "url": "https://github.com/ai-dynamo/frontend-crates",
        "local_repository_ids": [],
        "pull_request_ids": [],
    }
    monkeypatch.setattr(metadata, "linear_issue_metadata", lambda identifier, _cache, allow_network=True: {"identifier": identifier})

    metadata.associate_branch_pull_request(
        graph,
        hosted_repository_id=hosted_id,
        local_branch_id=None,
        pull_request={
            "number": 93,
            "title": "fork branch deleted locally",
            "description": "",
            "linear_ids": [],
            "head_repository": {"full_name": "contributor/frontend-crates", "url": "https://github.com/contributor/frontend-crates"},
            "head_branch_name": "deleted-feature",
        },
        cache=MetadataCache(),
        allow_network=False,
    )

    pr = graph["pull_requests"]["pull-request:hosted-repository:github.com/ai-dynamo/frontend-crates:93"]
    assert pr["local_branch_ids"] == []
    assert pr["head_hosted_repository_id"] == "hosted-repository:github.com/contributor/frontend-crates"
    assert graph["hosted_repositories"][hosted_id]["pull_request_ids"] == [pr["id"]]
    metadata.validate_work_graph(graph)


def test_session_work_graph_deduplicates_linear_issue_across_branch_and_multiple_prs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:ai-dynamo/frontend-crates.git")
    _git(repo, "checkout", "-b", "feature/DIS-4000")
    _git(repo, "branch", "feature/other")
    pane = _pane("s-linear-graph", 0, repo)
    info = SessionInfo(session="s-linear-graph", panes=[pane], selected_pane=pane, agents=[])
    metadata_cache = MetadataCache()
    calls = []

    monkeypatch.setattr(metadata, "github_pull_requests_by_branch", lambda _repo, branch, _cache, allow_network=True: [
        {"number": 94, "title": "DIS-4000 old", "description": "DIS-4001", "linear_ids": ["DIS-4000", "DIS-4001"]},
        {"number": 95, "title": "DIS-4000 current", "description": "DIS-4002", "linear_ids": ["DIS-4000", "DIS-4002"]},
    ] if branch == "feature/other" else [])
    monkeypatch.setattr(metadata, "linear_issue_metadata", lambda identifier, _cache, allow_network=True: calls.append(identifier) or {"identifier": identifier})

    graph = metadata.session_work_graph(info, metadata_cache, allow_network=True)
    branch = next(branch for branch in graph["local_branches"].values() if branch["name"] == "feature/other")
    assert branch["linear_issue_ids"] == ["DIS-4000", "DIS-4001", "DIS-4002"]
    assert set(graph["linear_issues"]) == {"DIS-4000", "DIS-4001", "DIS-4002"}
    assert calls.count("DIS-4000") == 1
    assert {issue_id for pr in graph["pull_requests"].values() for issue_id in pr["linear_issue_ids"]} == {"DIS-4000", "DIS-4001", "DIS-4002"}


def test_session_work_graph_many_observations_reuse_one_git_inventory_and_compact_entities(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    pane = _pane("s-observation-pressure", 0, repo)
    agent = AgentInfo("s-observation-pressure", "codex", 101, pane.target, "codex", str(repo), "running", "a", None, None)
    info = SessionInfo(session="s-observation-pressure", panes=[pane], selected_pane=pane, agents=[agent])
    home_like = tmp_path / "home"
    home_like.mkdir()
    changed_paths = {str(repo / f"nested/{index}/file.py"): {"M"} for index in range(40)}
    for path in changed_paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("x\n", encoding="utf-8")
    original_inventory = metadata.git_inventory
    calls = []

    def counted_inventory(path, **kwargs):
        calls.append(path)
        return original_inventory(path, **kwargs)

    monkeypatch.setattr(metadata, "scan_agent_changes", lambda _agent: changed_paths)
    monkeypatch.setattr(metadata, "session_workdir", lambda session: home_like)
    monkeypatch.setattr(metadata, "numbered_session_workdir", lambda session: None)
    monkeypatch.setattr(metadata, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})
    monkeypatch.setattr(metadata, "git_inventory", counted_inventory)
    # Exercise the actual session-metadata API projection: repeated observed
    # files must not cause a fresh Git inventory or a repeated entity graph.
    payload = metadata.session_to_json(info, MetadataCache(), allow_network=False)
    graph = payload["work_graph"]
    serialized = json.dumps(graph, sort_keys=True)
    payload_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))

    assert len(graph["path_observations"]) >= 40
    assert len(graph["git_worktrees"]) == 1
    assert len(graph["local_repositories"]) == 1
    assert len(calls) == 1
    assert serialized.count('"common_git_dir"') == 1
    assert len(serialized) < 110_000
    # This is the JSON body served by `/api/session-metadata`, including its
    # temporary graph-derived compatibility projections. The cap proves that
    # 40 observations do not multiply branch inventories in the wire payload.
    assert payload_bytes < 140_000


def test_branch_linear_metadata_includes_pr_and_branch_ids(monkeypatch):
    seen = []

    def fake_linear_issue_metadata(identifier, cache, allow_network=True):
        seen.append((identifier, allow_network))
        return {
            "identifier": identifier,
            "title": f"{identifier} title",
            "state": "In Progress",
            "url": f"https://linear.test/{identifier}",
            "source": "test",
        }

    monkeypatch.setattr(metadata, "linear_issue_metadata", fake_linear_issue_metadata)
    git_data = {
        "other_branches": {
            "branches": [
                {
                    "name": "keivenc/DIS-2212__frontend",
                    "current": False,
                    "subject": "branch subject references DIS-2213",
                    "pull_request": {
                        "title": "PR mentions DIS-2214",
                        "description": "body mentions DIS-2215",
                        "linear_ids": ["DIS-2216"],
                    },
                },
                {"name": "main", "current": False, "subject": "main"},
            ],
        },
    }

    metadata.enrich_branch_linear_metadata(git_data, MetadataCache(), allow_network=True)
    branch = git_data["other_branches"]["branches"][0]

    assert branch["linear_ids"] == ["DIS-2212", "DIS-2213", "DIS-2214", "DIS-2215", "DIS-2216"]
    assert [item["identifier"] for item in branch["linear"]] == branch["linear_ids"]
    assert branch["linear"][0]["title"] == "DIS-2212 title"
    assert seen == [(identifier, True) for identifier in branch["linear_ids"]]
    assert git_data["other_branches"]["branches"][1]["linear"] == []

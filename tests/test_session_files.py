import json
import os
import time


from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib import session_files

from _git_helpers import git


def agent(kind, transcript, cwd):
    return AgentInfo(
        session="s1",
        kind=kind,
        pid=1,
        pane_target="%1",
        command=kind,
        cwd=str(cwd),
        status=None,
        session_id=None,
        transcript=str(transcript),
        error=None,
    )


def test_scans_claude_and_codex_tool_changes(tmp_path):
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}},
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/new.md"}},
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text(
        '{"msg":"*** Begin Patch\\n*** Add File: src/new.py\\n*** Update File: src/app.py\\n*** Delete File: old.py\\n"}\n',
        encoding="utf-8",
    )

    claude = session_files.scan_claude_transcript(claude_path, str(tmp_path))
    codex = session_files.scan_codex_transcript(codex_path, str(tmp_path))

    assert claude[str(tmp_path / "src" / "app.py")] == {"M"}
    assert claude["/tmp/new.md"] == {"A"}
    assert codex[str(tmp_path / "src" / "new.py")] == {"A"}
    assert codex[str(tmp_path / "src" / "app.py")] == {"M"}
    assert codex[str(tmp_path / "old.py")] == {"D"}


def test_session_files_payload_merges_tool_attribution_with_git_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")
    untracked = repo / "new.txt"
    untracked.write_text("new\n", encoding="utf-8")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        '{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n*** Add File: new.txt\\n"}\n',
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["tracked.txt"]["repo"] == str(repo)
    assert by_path["tracked.txt"]["agent"] == "codex"
    assert by_path["tracked.txt"]["agents"] == ["codex"]  # C5: full agent list, scalar `agent` is an alias
    assert by_path["tracked.txt"]["size"] == (repo / "tracked.txt").stat().st_size  # C5: size for image-preview gating
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 1
    # new.txt is untracked (never `git add`ed) -> "?", distinct from a staged/committed add "A".
    assert by_path["new.txt"]["status"] == "?"
    assert by_path["new.txt"]["added"] == 1
    assert by_path["new.txt"]["removed"] == 0
    assert payload["repos"] == [{"repo": str(repo), "count": 2, "touched_count": 2, "added": 2, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_keeps_transcript_paths_when_branch_is_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "branch", "-M", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("merged\n", encoding="utf-8")
    git(repo, "commit", "-am", "merged change")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    os.utime(rollout, (1500, 1500))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=2000)

    assert len(payload["files"]) == 1
    item = payload["files"][0]
    assert item["session"] == "s1"
    assert item["agents"] == ["codex"]
    assert item["agent"] == "codex"
    assert item["status"] == "T"
    assert item["repo"] == str(repo)
    assert item["path"] == "tracked.txt"
    assert item["abs_path"] == str(tracked)
    assert item["mtime"] == 1500
    assert item["source"] == "transcript"
    assert item["added"] is None
    assert item["removed"] is None
    assert item["uploaded"] is False

    assert len(payload["repos"]) == 1
    repo_summary = payload["repos"][0]
    assert repo_summary["repo"] == str(repo)
    assert repo_summary["count"] == 1
    assert repo_summary["touched_count"] == 1
    assert repo_summary["added"] == 0
    assert repo_summary["removed"] == 0
    assert repo_summary["from_ref"] == "default"
    assert repo_summary["to_ref"] == "base"
    assert repo_summary["error"] == ""


def test_session_files_payload_collects_multiple_agents_for_one_file(tmp_path):
    # C5: when both Claude and Codex touch the same file, the entry lists BOTH (no overwrite), so the UI
    # can render two agent icons.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tracked)}},
        ]}}) + "\n",
        encoding="utf-8",
    )
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(
        session="s1", panes=[], selected_pane=None,
        agents=[agent("claude", claude_path, repo), agent("codex", rollout, repo)],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert sorted(by_path["tracked.txt"]["agents"]) == ["claude", "codex"]
    assert by_path["tracked.txt"]["agent"] in {"claude", "codex"}  # scalar alias is just the first


def test_session_files_payload_excludes_non_repo_transcript_artifacts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")
    tmp_artifact = tmp_path / "scratch.txt"
    tmp_artifact.write_text("scratch\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        f'{{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n*** Add File: {tmp_artifact}\\n"}}\n',
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert [item["path"] for item in payload["files"]] == ["tracked.txt"]
    assert all(not str(item["abs_path"]).startswith(str(tmp_path / "scratch")) for item in payload["files"])


def test_git_status_parses_renames_and_tab_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    old_name = "old\tname.txt"
    new_name = "new\tname.txt"
    (repo / old_name).write_text("one\n", encoding="utf-8")
    git(repo, "add", old_name)
    git(repo, "commit", "-m", "base")
    git(repo, "mv", old_name, new_name)

    statuses, error = session_files.git_name_status(repo, "HEAD")
    counts = session_files.git_numstat(repo, "HEAD")

    assert error == ""
    assert old_name not in statuses
    assert statuses[new_name] == "R"
    assert counts[new_name] == {"added": 0, "removed": 0}


def test_git_status_labels_untracked_question_distinct_from_staged_add_A(tmp_path):
    # An untracked working-tree file must read as "?" (git's own untracked marker), while a genuinely
    # staged add reads as "A", so the changes pane can tell "git is tracking this add" apart from
    # "this file isn't tracked yet".
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "loose.txt").write_text("loose\n", encoding="utf-8")  # untracked, never added

    statuses, error = session_files.git_name_status(repo, "HEAD")

    assert error == ""
    assert statuses["staged.txt"] == "A"
    assert statuses["loose.txt"] == "?"


def test_session_files_payload_preserves_untracked_symlink_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    target = repo / "lib" / "parsers" / "REASONING_CASES.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Reasoning\n", encoding="utf-8")
    git(repo, "add", "lib/parsers/REASONING_CASES.md")
    git(repo, "commit", "-m", "base")
    staged_link = repo / ".stage-v2" / "lib" / "parsers" / "REASONING_CASES.md"
    staged_link.parent.mkdir(parents=True)
    staged_link.symlink_to(target)
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert "lib/parsers/REASONING_CASES.md" not in by_path
    assert by_path[".stage-v2/lib/parsers/REASONING_CASES.md"]["status"] == "?"
    assert by_path[".stage-v2/lib/parsers/REASONING_CASES.md"]["abs_path"] == str(staged_link)


def test_git_numstat_parses_paths_with_tabs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    name = "tab\tpath.txt"
    (repo / name).write_text("one\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", "base")
    (repo / name).write_text("one\ntwo\n", encoding="utf-8")

    counts = session_files.git_numstat(repo, "HEAD")

    assert counts[name] == {"added": 1, "removed": 0}


def test_session_files_payload_marks_generated_upload_names(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    upload = repo / "20260531-001-diagram.png"
    upload.write_bytes(b"png")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "20260531-001-diagram.png"
    assert payload["files"][0]["uploaded"] is True


def test_session_files_payload_counts_branch_commits_since_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "branch", "-M", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "feature")
    tracked.write_text("feature\n", encoding="utf-8")
    git(repo, "commit", "-am", "feature change")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 1
    assert payload["repos"] == [{"repo": str(repo), "count": 1, "touched_count": 1, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_accepts_explicit_commit_refs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "two")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=older, to_ref=newer)

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["added"] == 1
    assert payload["files"][0]["removed"] == 1
    assert payload["from_ref"] == older
    assert payload["to_ref"] == newer
    assert payload["repos"][0]["behind"] == 0
    assert payload["repos"][0]["ahead"] == 1


def test_session_files_payload_explicit_current_ref_matches_plain_git_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    (repo / "loose.txt").write_text("not in plain git diff\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: loose.txt\\n"}\n', encoding="utf-8")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=older, to_ref="current")

    assert [item["path"] for item in payload["files"]] == ["tracked.txt"]
    assert payload["files"][0]["added"] == 1
    assert payload["files"][0]["removed"] == 0
    assert payload["repos"][0]["count"] == 1
    assert payload["repos"][0]["added"] == 1
    assert payload["repos"][0]["removed"] == 0


def test_git_numstat_does_not_use_copy_detection_for_plain_diff_counts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    source = repo / "source.txt"
    source.write_text("a\nb\nc\nd\ne\nf\ng\nh\n", encoding="utf-8")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "base")
    copied = repo / "copied.txt"
    copied.write_text("a\nb\nc\nd\ne\nf\ng\nchanged\nnew\n", encoding="utf-8")
    git(repo, "add", "copied.txt")

    counts = session_files.git_numstat(repo, "HEAD")

    assert counts["copied.txt"] == {"added": 9, "removed": 0}


def test_session_files_payload_falls_back_when_requested_ref_is_unknown_in_repo(tmp_path):
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "one")
        tracked.write_text("two\n", encoding="utf-8")
    repo1_from = git(repo1, "rev-parse", "HEAD").stdout.strip()
    panes = []
    for index, repo in enumerate((repo1, repo2)):
        panes.append(
            PaneInfo(
                session="s1",
                window="0",
                pane=str(index),
                pane_id=f"%{index}",
                target=f"s1:0.{index}",
                current_path=str(repo),
                command="zsh",
                active=index == 0,
                window_active=True,
                title="",
                pid=11 + index,
            )
        )
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=repo1_from, to_ref="current")

    assert payload["errors"] == []
    assert {item["repo"] for item in payload["files"]} == {str(repo1), str(repo2)}
    assert all(item["path"] == "tracked.txt" for item in payload["files"])


def test_session_files_payload_applies_per_repo_refs_independently(tmp_path):
    # C6: a FROM/TO override scoped to repo1 must NOT change repo2's comparison — each repo reports its
    # own effective refs.
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "one")
        tracked.write_text("two\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "two")
        tracked.write_text("three\n", encoding="utf-8")
    repo1_from = git(repo1, "rev-parse", "HEAD~1").stdout.strip()
    panes = []
    for index, repo in enumerate((repo1, repo2)):
        panes.append(
            PaneInfo(
                session="s1", window="0", pane=str(index), pane_id=f"%{index}",
                target=f"s1:0.{index}", current_path=str(repo), command="zsh",
                active=index == 0, window_active=True, title="", pid=11 + index,
            )
        )
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    payload = session_files.session_files_payload_for_info(
        info, hours=24, now=time.time(),
        repo_refs={str(repo1): {"from": repo1_from, "to": "current"}},
    )
    by_repo = {item["repo"]: item for item in payload["repos"]}

    assert by_repo[str(repo1)]["from_ref"] == repo1_from
    assert by_repo[str(repo1)]["to_ref"] == "current"
    assert by_repo[str(repo1)]["error"] == ""
    # repo2 had no override, so it stays on the default comparison and is not affected by repo1's SHA.
    assert by_repo[str(repo2)]["from_ref"] == "default"
    assert by_repo[str(repo2)]["to_ref"] == "base"
    assert payload["errors"] == []


def test_git_recent_refs_exposes_more_than_twenty_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    for index in range(25):
        tracked.write_text(f"{index}\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", f"commit {index}")

    refs = session_files.git_recent_refs(repo)

    assert refs[0]["ref"] == "HEAD"
    assert refs[1]["ref"] == "current"
    assert len(refs) >= 27
    assert any(item["subject"] == "commit 0" for item in refs)


def test_session_files_payload_reports_invalid_ref_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "two")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=newer, to_ref=older)

    assert payload["files"] == []
    assert payload["errors"] == []


def test_session_files_payload_uses_session_repo_without_ai_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("working\n", encoding="utf-8")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["source"] == "git"
    assert payload["repos"] == [{"repo": str(repo), "count": 1, "touched_count": 0, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_does_not_invent_agent_for_repo_only_change(tmp_path):
    # C5: a git change with NO transcript attribution (the rollout never mentions this file) must render
    # zero agent icons — earlier the code invented a fallback to the session's agent, falsely implying
    # the agent touched a file the user changed by hand.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("working\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"no patch path here"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["agents"] == []
    assert payload["files"][0]["agent"] == ""
    assert payload["files"][0]["source"] == "git"

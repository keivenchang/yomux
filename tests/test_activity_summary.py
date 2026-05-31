import json
import subprocess
import time

from yolomux_lib.activity_summary import activity_signature
from yolomux_lib.activity_summary import build_global_activity_summary
from yolomux_lib.activity_summary import build_session_activity_summary
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.session_files import session_files_payload_for_info


def make_agent(session, transcript, cwd):
    return AgentInfo(
        session=session,
        kind="codex",
        pid=123,
        pane_target=f"{session}:0.0",
        command="codex",
        cwd=str(cwd),
        status="running",
        session_id="sid",
        transcript=str(transcript),
        error=None,
        model="gpt-5.5",
    )


def test_activity_summary_reports_agent_repo_goal_and_file_counts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    target = repo / "app.py"
    target.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    target.write_text("new\n", encoding="utf-8")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-05-31T12:00:00Z", "payload": {"type": "user_message", "message": "Fix editor colors"}}),
            "*** Update File: app.py",
            json.dumps({"timestamp": "2026-05-31T12:00:01Z", "payload": {"type": "agent_message", "message": "Editing app.py"}}),
        ]),
        encoding="utf-8",
    )
    now = time.time()
    info = SessionInfo(
        session="5",
        panes=[
            PaneInfo(
                session="5",
                window="0",
                pane="0",
                pane_id="%1",
                target="5:0.0",
                current_path=str(repo),
                command="zsh",
                active=True,
                window_active=True,
                title="",
                pid=999,
            )
        ],
        selected_pane=None,
        agents=[make_agent("5", transcript, repo)],
    )
    files = session_files_payload_for_info(info, hours=24, now=now)
    project = {"git": {"root": str(repo), "branch": "main", "dirty_count": 1}, "pull_request": {"checks": {"state": "failing", "summary": "CI failing"}}, "linear": []}

    summary = build_session_activity_summary(info, project, files)

    assert summary["session"] == "5"
    assert summary["agent"] == "codex"
    assert summary["goal"] == "Fix editor colors"
    assert summary["files"] == {"count": 1, "added": 1, "removed": 1}
    assert summary["local"].startswith("Codex gpt-5.5 session 5 is")
    assert "The changes are 1 file changed (+1/-1)." in summary["local"]
    assert "Status: CI failing; 1 dirty file." in summary["local"]
    assert "CI failing" in summary["lines"]
    assert any("app.py" in line for line in summary["file_lines"])
    assert activity_signature(info, project, files)["files"][0][2] == "app.py"


def test_global_activity_summary_rolls_up_sessions():
    global_summary = build_global_activity_summary([
        {"session": "1", "agent": "codex", "active": True, "repos": ["/repo/a"], "files": {"count": 2, "added": 5, "removed": 1}, "work": "fix A"},
        {"session": "2", "agent": "claude", "active": False, "repos": ["/repo/b"], "files": {"count": 1, "added": 0, "removed": 3}, "goal": "debug B"},
    ])

    assert global_summary["active_agents"] == 1
    assert global_summary["files"] == {"count": 3, "added": 5, "removed": 4}
    assert global_summary["headline"].startswith("You've worked on fix A and debug B.")
    assert "The changes are 3 files changed (+5/-4)" in global_summary["lines"][0]

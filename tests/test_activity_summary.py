import json
import os
import time


from yolomux_lib.activity_summary import activity_signature
from yolomux_lib.activity_summary import build_global_activity_summary
from yolomux_lib.activity_summary import build_session_activity_summary
from yolomux_lib.activity_summary import build_yoagent_chat_prompt
from yolomux_lib.activity_summary import build_yoagent_resume_prompt
from yolomux_lib.activity_summary import deterministic_yoagent_reply
from yolomux_lib.activity_summary import transcript_file_signature
from yolomux_lib.activity_summary import yolomux_help_primer
from yolomux_lib.activity_summary import yoagent_context_lines
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.session_files import session_files_payload_for_info

from _git_helpers import git, init_repo


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
    init_repo(repo)
    target = repo / "app.py"
    target.write_text("old\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "base")
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
    assert summary["activity_label"] == "recently active"
    assert summary["active"] is True
    assert summary["local"].startswith("Codex gpt-5.5 session 5 is recently active")
    assert "You last worked on this session" in summary["local"]
    assert summary["last_activity_text"]
    assert summary["last_activity_ts"]
    assert "It currently has 1 file changed (+1/-1)." in summary["local"]
    assert "Recent files: M app.py (+1/-1)." in summary["local"]
    assert "Status check: CI failing; 1 dirty file." in summary["local"]
    assert "CI failing" in summary["lines"]
    assert any("app.py" in line for line in summary["file_lines"])
    signature = activity_signature(info, project, files)
    assert signature["summary_format"] >= 2
    assert signature["files"][0][2] == "app.py"


def test_session_activity_summary_only_calls_stale_sessions_idle(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("agent finished\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(transcript, (old, old))
    info = SessionInfo(
        session="8",
        panes=[],
        selected_pane=None,
        agents=[make_agent("8", transcript, repo)],
    )

    summary = build_session_activity_summary(info, {"git": {"root": str(repo), "branch": "main"}}, {"files": []})

    assert summary["activity_label"] == "idle"
    assert summary["active"] is False
    assert "session 8 is idle" in summary["local"]
    assert "just now" not in summary["local"]


def test_global_activity_summary_rolls_up_sessions():
    old_ts = time.time() - 15 * 24 * 3600
    global_summary = build_global_activity_summary([
        {"session": "1", "agent": "codex", "active": True, "repos": ["/repo/a"], "files": {"count": 2, "added": 5, "removed": 1}, "work": "fix A", "last_activity_text": "5 minutes ago", "last_activity_ts": time.time() - 300},
        {"session": "2", "agent": "claude", "active": False, "repos": ["/repo/b"], "files": {"count": 1, "added": 0, "removed": 3}, "goal": "debug B", "last_activity_text": "15 days ago", "last_activity_ts": old_ts},
    ])

    assert global_summary["active_agents"] == 1
    assert global_summary["files"] == {"count": 3, "added": 5, "removed": 4}
    assert global_summary["headline"].startswith("Your most recent work is about fix A")
    assert "currently making changes to a and b in order to finish fix A" in global_summary["headline"]
    assert "Other work includes debug B" in global_summary["headline"]
    assert "So far: 3 files changed (+5/-4)" in global_summary["lines"][0]
    assert "Recommendation: keep session 1 focused on fix A" in global_summary["lines"][1]
    assert "You have not touched session 2" in global_summary["lines"][2]
    assert "ask it to summarize before resuming" in global_summary["lines"][2]


def test_transcript_file_signature_uses_nanosecond_mtime(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("first\n", encoding="utf-8")
    base_ns = 1_900_000_000_000_000_000
    transcript.touch()

    os.utime(transcript, ns=(base_ns, base_ns))
    agent = make_agent("5", transcript, repo)
    first = transcript_file_signature(agent)

    transcript.write_text("secnd\n", encoding="utf-8")
    os.utime(transcript, ns=(base_ns + 1, base_ns + 1))
    second = transcript_file_signature(agent)

    assert first["size"] == second["size"]
    assert first["mtime"] == second["mtime"]
    assert first["mtime_ns"] != second["mtime_ns"]


def test_yoagent_prompt_and_deterministic_reply_use_activity_context():
    activity = {
        "global": {
            "headline": "Your most recent work is about editor fixes, and you are currently making changes to yolomux in order to finish editor fixes. So far: 2 files changed (+7/-1); 1 of 1 AI agent is active.",
            "lines": [
                "Your most recent work is about editor fixes, and you are currently making changes to yolomux in order to finish editor fixes. So far: 2 files changed (+7/-1); 1 of 1 AI agent is active.",
                "Recommendation: keep session 5 focused on editor fixes until it reaches a clean stopping point, last worked 2 hours ago.",
            ],
        },
        "sessions": {
            "5": {
                "agent": "codex",
                "agent_label": "Codex gpt-5.5",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 2, "added": 7, "removed": 1},
                "work": "editor fixes",
                "status_text": "CI pending",
                "file_lines": ["M static/yolomux.js (+5/-1)"],
                "local": "Codex session 5 is active in yolomux.",
            }
        },
        "errors": [],
    }
    settings = {"system_prompt": "Use facts only.", "intro": "Be terse.", "format": "One sentence."}

    lines = yoagent_context_lines(activity)
    prompt = build_yoagent_chat_prompt(
        "what changed?",
        activity,
        settings,
        [
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "old2"},
            {"role": "user", "content": "old3"},
            {"role": "assistant", "content": "old4"},
            {"role": "user", "content": "status?"},
        ],
    )
    changed_resume = build_yoagent_resume_prompt("what now?", activity, settings, True)
    unchanged_resume = build_yoagent_resume_prompt("what now?", activity, settings, False)
    reply = deterministic_yoagent_reply("session 5", activity, settings)

    activity["sessions"]["5"]["last_activity_text"] = "2 hours ago"
    lines = yoagent_context_lines(activity)

    assert any("session 5: Codex gpt-5.5 is active" in line for line in lines)
    assert any("last worked: 2 hours ago" in line for line in lines)
    assert "Use facts only." in prompt
    assert "Do not run tools" in prompt
    assert "transcript directories" in prompt
    assert "YOLOmux concepts:" in prompt
    assert "Pane" in prompt
    assert "Context sourcing chain" in prompt
    assert "M static/yolomux.js" in prompt
    assert "old1" not in prompt
    assert "status?" in prompt
    assert "Activity summary changed" in changed_resume
    assert "YOLOmux concepts:" in changed_resume
    assert "Do not run tools" in changed_resume
    assert "M static/yolomux.js" in changed_resume
    assert "Activity summary is unchanged" in unchanged_resume
    assert "M static/yolomux.js" not in unchanged_resume
    assert "No AI backend is answering" in reply
    assert "Set or log in a Claude/Codex backend" in reply
    assert "Your most recent work is about editor fixes" in reply
    # Direct status shape: focus on the asked-about session plus an Open / pending tail.
    assert "**1. editor fixes" in reply
    assert "(session 5)**" in reply
    assert "- Codex gpt-5.5 is active" in reply
    assert "**Open / pending:**" in reply
    assert "Recommendation" in reply
    assert "Be terse" not in reply


def test_yoagent_help_primer_and_deterministic_help_answers():
    primer = yolomux_help_primer()
    assert "YOLOmux help primer from README.md" in primer
    assert "Pane" in primer
    assert "Finder" in primer
    assert "Context sourcing chain" in primer

    pane_reply = deterministic_yoagent_reply("what's a pane?", {}, {})
    assert "YOLOmux Pane" in pane_reply
    assert "tmux pane is different" in pane_reply

    window_reply = deterministic_yoagent_reply("difference between a tmux window and a YOLOmux tab?", {}, {})
    assert "tmux window" in window_reply
    assert "YOLOmux Tab" in window_reply

    context_reply = deterministic_yoagent_reply("where do your insights come from?", {}, {})
    assert "transcript JSONL" in context_reply
    assert "no detected agent or no transcript" in context_reply

    fallback_reply = deterministic_yoagent_reply("date?", {"global": {"headline": "No recent work."}}, {})
    assert fallback_reply.startswith("No AI backend is answering")
    assert "No recent work." in fallback_reply


def test_deterministic_yoagent_reply_prioritizes_active_work_without_listing_every_session():
    now = time.time()
    activity = {
        "global": {"headline": "Two AI agents are working across yolomux and dynamo.", "lines": []},
        "sessions": {
            "5": {
                "session": "5",
                "agent": "codex",
                "agent_label": "Codex gpt-5.5",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 3, "added": 12, "removed": 4},
                "work": "tab approval badge",
                "pr_number": 12345,
                "ci": "CI passing",
                "status_text": "PR #12345",
                "last_activity_text": "2 minutes ago",
                "last_activity_ts": now - 120,
            },
            "9": {
                "session": "9",
                "agent": "claude",
                "agent_label": "Claude opus",
                "active": False,
                "repos": ["/repo/dynamo"],
                "files": {"count": 0, "added": 0, "removed": 0},
                "work": "stale refactor",
                "status_text": "waiting for more activity",
                "last_activity_text": "8 days ago",
                "last_activity_ts": now - 8 * 24 * 3600,
            },
        },
        "errors": [],
    }
    reply = deterministic_yoagent_reply("what is happening?", activity, {})
    # Default answers prioritize active/fresh work as topic/advice, without exposing session ids.
    assert "**Priority:**" in reply
    assert "- **tab approval badge — yolomux · PR #12345:**" in reply
    assert "3 files changed (+12/-4)" in reply
    assert "CI passing" in reply
    assert "(session 5)" not in reply
    assert "session 9" not in reply
    # Closing Open / pending section gives advice without default session inventory.
    assert "**Open / pending:**" in reply
    assert "Keep the active work" in reply
    assert "Stale work: stale refactor" in reply.split("**Open / pending:**", 1)[1]
    list_reply = deterministic_yoagent_reply("list all sessions", activity, {})
    assert "**1. tab approval badge — yolomux · PR #12345 (session 5)**" in list_reply
    assert "**2. stale refactor — dynamo (session 9)**" in list_reply
    assert list_reply.index("(session 5)") < list_reply.index("(session 9)")
    named_reply = deterministic_yoagent_reply("what is session 5 doing?", activity, {})
    assert "**1. tab approval badge — yolomux · PR #12345 (session 5)**" in named_reply
    assert "(session 9)" not in named_reply


def test_changed_file_totals_coerces_numeric_strings_and_ignores_bools():
    from yolomux_lib.activity_summary import changed_file_totals
    # DOIT.6 #78: numeric strings count ("5" -> 5); a bool does NOT (added=True must not be +1).
    payload = {"files": [
        {"added": "5", "removed": "2"},
        {"added": 3, "removed": 1},
        {"added": True, "removed": None},
        {"added": "x", "removed": "y"},
    ]}
    totals = changed_file_totals(payload)
    assert totals == {"count": 4, "added": 8, "removed": 3}

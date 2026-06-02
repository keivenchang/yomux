from http import HTTPStatus
import json
import threading
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo


@pytest.fixture(autouse=True)
def no_control_socket(monkeypatch):
    monkeypatch.setattr(app_module.YolomuxControlServer, "start", lambda self: None)
    monkeypatch.setattr(app_module.YolomuxControlServer, "stop", lambda self: None)


def test_auto_approve_status_refreshes_session_order(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session_order"] == ["new"]
    assert payload["sessions"] == {"new": {"target": "new"}}


def test_auto_approve_roster_uses_live_pane_working_signal(monkeypatch):
    # #28: the roster's working/idle signal comes from the LIVE pane (a cheap visible-only capture),
    # not transcript recency, while still discovering once and skipping the expensive prompt fan-out.
    info = SessionInfo(session="6", panes=[], selected_pane=None, agents=[])
    discover_calls = []
    capture_calls = []
    pane_text = {"5": "working pane", "6": "idle pane"}
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5", "6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (discover_calls.append(tuple(sessions)) or {"5": info, "6": info}, []))

    def fake_capture(session, *_args, **kwargs):
        capture_calls.append((session, kwargs.get("visible_only")))
        return pane_text.get(session, "")

    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", fake_capture)
    monkeypatch.setattr(app_module.auto_approve_tmux, "agent_screen_state", lambda text: {"key": "working" if text == "working pane" else "idle", "text": ""})
    monkeypatch.setattr(app_module.auto_approve_tmux, "hybrid_approval_prompt_state", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("roster must not run the prompt-detection fan-out")))
    monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda _session: None)
    webapp = app_module.TmuxWebtermApp(["5", "6"])
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert discover_calls == [("5", "6")]  # discovered once for the whole roster, not per session
    assert {session for session, _visible in capture_calls} == {"5", "6"}
    assert all(visible_only is True for _session, visible_only in capture_calls)  # cheap visible-only capture only
    assert payload["sessions"]["5"]["screen"]["key"] == "working"  # live working pane spins
    assert payload["sessions"]["6"]["screen"]["key"] == "idle"  # finished agent idle at prompt stops spinning
    assert payload["sessions"]["5"]["prompt"]["visible"] is False  # no live prompt fan-out in the roster


def test_transcripts_payload_exposes_server_version(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    try:
        payload = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert payload["server_version"] == app_module.YOLOMUX_VERSION


def test_metadata_badge_pulse_expiry_does_not_persist(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    signature = {"main": "", "pr": "123", "status": "open", "ci": "pending"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_signatures = {"6": dict(signature)}
    webapp.metadata_badge_pulse_until = {"6": {"ci": 99.0}}
    try:
        payloads = {"6": {}}
        webapp.apply_metadata_badge_pulses(payloads)
    finally:
        webapp.control_server.stop()

    assert persist_calls == []
    assert webapp.metadata_badge_pulse_until == {}
    assert "metadata_badge_pulse_remaining_ms" not in payloads["6"]


def test_metadata_badge_signature_change_persists(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    next_signature = {"main": "", "pr": "123", "status": "merged", "ci": "passing"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: next_signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_signatures = {"6": {"main": "", "pr": "123", "status": "open", "ci": "pending"}}
    try:
        webapp.apply_metadata_badge_pulses({"6": {}})
    finally:
        webapp.control_server.stop()

    assert persist_calls == ["persist"]
    assert webapp.metadata_badge_signatures == {"6": next_signature}


def test_prompt_and_screen_status_uses_transcript_activity_when_visible_pane_is_idle(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", lambda session, visible_only=False: "❯ ")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is False
    assert screen["key"] == "working"
    assert "Bash" in screen["text"]


def test_prompt_and_screen_status_reports_os_errors(monkeypatch):
    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tmux failed")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["error"] == "tmux failed"
    assert screen == {"key": "error", "text": "tmux failed"}


def test_prompt_and_screen_status_does_not_hide_programmer_errors(monkeypatch):
    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", lambda *_args, **_kwargs: "visible")
    monkeypatch.setattr(app_module.auto_approve_tmux, "hybrid_approval_prompt_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        with pytest.raises(RuntimeError, match="bug"):
            webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()


def test_activity_summary_payload_reuses_cached_session_summary(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "Fix tabs"}}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    files_payload = {"files": [{"status": "M", "repo": str(tmp_path), "path": "README.md", "abs_path": str(tmp_path / "README.md"), "added": 1, "removed": 0, "mtime": 10}], "repos": [{"repo": str(tmp_path), "count": 1}], "errors": []}
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "session_project_metadata", lambda info, cache, allow_network=False: {"git": {"root": str(tmp_path), "branch": "main", "dirty_count": 1}, "pull_request": None, "linear": []})
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda info, hours=24.0: files_payload)

    def fake_build(info, project, files):
        calls.append(info.session)
        return {"session": info.session, "agent": "codex", "active": False, "repos": [str(tmp_path)], "files": {"count": 1, "added": 1, "removed": 0}, "lines": ["cached test"], "local": "cached test"}

    monkeypatch.setattr(app_module, "build_session_activity_summary", fake_build)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    try:
        first = webapp.activity_summary_payload()
        second = webapp.activity_summary_payload()
        third = webapp.activity_summary_payload(force=True)
    finally:
        webapp.control_server.stop()

    assert calls == ["5", "5"]
    assert first["global"]["files"] == {"count": 1, "added": 1, "removed": 0}
    assert second["sessions"]["5"]["local"] == "cached test"
    assert third["sessions"]["5"]["local"] == "cached test"


def test_yoagent_chat_uses_deterministic_fallback(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {
            "5": {
                "session": "5",
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "local": "Codex session 5 is active in yolomux.",
            }
        },
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "what is session 5 doing?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is False
    assert "editor fixes" in payload["answer"]
    assert "session 5" in payload["context_lines"][1]


def test_yoagent_cli_auth_failure_is_actionable(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "claude", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False: ("", "Error: not logged in. Run claude login."))
    try:
        payload, status = webapp.yoagent_chat({"message": "status?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend"] == "claude"
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is True
    assert "Claude CLI is not logged in" in payload["fallback_reason"]
    assert "claude auth login" in payload["fallback_reason"]


def test_yoagent_cli_fallback_keeps_non_auth_error():
    reason = app_module.yoagent_cli_fallback_reason("codex", "model overloaded")
    assert reason == "model overloaded"


def test_yoagent_permission_block_answer_falls_back(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False: ("I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl.", ""))
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Your most recent work is about editor fixes."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == ""
    assert "outside the supplied YOLOmux context" in reason
    assert status["backend"] == "claude"


def test_reset_yoagent_chat_clears_cli_sessions():
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        webapp.yoagent_cli_sessions["claude"] = {"session_id": "old"}
        assert webapp.reset_yoagent_chat() == {"ok": True}
        assert webapp.yoagent_cli_sessions == {}
    finally:
        webapp.control_server.stop()


def test_yoagent_cli_backend_resumes_and_trims_context(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_claude(prompt, session_id="", resume=False):
        calls.append({"prompt": prompt, "session_id": session_id, "resume": resume})
        return ("seeded" if not resume else "resumed", "")

    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {
            "5": {
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "file_lines": ["M static/yolomux.js (+2/-0)"],
            }
        },
        "errors": [],
    }
    try:
        first, first_reason, first_status = webapp.run_yoagent_cli_backend("claude", "first?", activity, {}, [])
        second, second_reason, second_status = webapp.run_yoagent_cli_backend("claude", "second?", activity, {}, [{"role": "user", "content": "first?"}])
    finally:
        webapp.control_server.stop()

    assert first == "seeded"
    assert first_reason == ""
    assert second == "resumed"
    assert second_reason == ""
    assert calls[0]["resume"] is False
    assert calls[1]["resume"] is True
    assert calls[0]["session_id"] == calls[1]["session_id"]
    assert first_status["seeded"] is True
    assert second_status["resumed"] is True
    assert second_status["prompt_chars"] < first_status["prompt_chars"]
    assert "Activity summary is unchanged" in calls[1]["prompt"]
    assert "M static/yolomux.js" not in calls[1]["prompt"]


def test_yoagent_cli_backend_does_not_hold_state_lock_during_cli(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    observed = []

    def fake_claude(_prompt, session_id="", resume=False):
        def probe_lock():
            acquired = webapp.yoagent_cli_lock.acquire(timeout=0.1)
            observed.append(acquired)
            if acquired:
                webapp.yoagent_cli_lock.release()

        thread = threading.Thread(target=probe_lock)
        thread.start()
        thread.join()
        return ("answer", "")

    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == "answer"
    assert reason == ""
    assert observed == [True]
    assert status["backend"] == "claude"


def test_codex_event_session_id_extracts_common_shapes():
    assert app_module.codex_event_session_id({"type": "thread.started", "thread_id": "abc"}) == "abc"
    assert app_module.codex_event_session_id({"thread": {"id": "nested"}}) == "nested"


def test_yoagent_codex_cli_persists_then_resumes(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_run(args, input, cwd, env, text, capture_output, timeout, check):
        calls.append(args)
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "codex-session"}),
            json.dumps({"type": "agent_message", "text": "answer"}),
        ])
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    try:
        first_answer, first_error, first_session = webapp.run_yoagent_codex_cli("first", resume=False)
        second_answer, second_error, second_session = webapp.run_yoagent_codex_cli("second", session_id=first_session, resume=True)
    finally:
        webapp.control_server.stop()

    assert first_answer == "answer"
    assert first_error == ""
    assert first_session == "codex-session"
    assert second_answer == "answer"
    assert second_error == ""
    assert second_session == "codex-session"
    assert calls[0][:3] == ["codex", "exec", "--json"]
    assert "--ephemeral" not in calls[0]
    assert "--sandbox" in calls[0]
    assert calls[1][:4] == ["codex", "exec", "resume", "--json"]
    assert "codex-session" in calls[1]
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    assert calls[1][calls[1].index("--sandbox") + 1] == "read-only"

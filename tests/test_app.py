from http import HTTPStatus
import json
import threading
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo


PROMPT_STATE_KEYS = set(app_module.auto_approve_tmux.blank_prompt_state())


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


def test_server_event_poll_seconds_accepts_fast_server_side_interval(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 100}}},
        )
        assert webapp.server_event_poll_seconds() == 0.25
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 850}}},
        )
        assert webapp.server_event_poll_seconds() == 0.85
    finally:
        webapp.control_server.stop()


def test_server_directory_event_poll_seconds_uses_own_interval(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 250, "server_directory_event_poll_ms": 1250}}},
        )
        assert webapp.server_event_poll_seconds() == 0.25
        assert webapp.server_directory_event_poll_seconds() == 1.25
    finally:
        webapp.control_server.stop()


def test_client_event_watch_sleep_uses_next_due_preference(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 250}}},
        )
        webapp.client_event_next_file_poll_at = 100.5
        webapp.client_event_next_signature_poll_at = 100.25
        webapp.client_event_next_auto_poll_at = 101.0
        webapp.client_event_next_watched_pr_poll_at = 200.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(0.25)
        webapp.client_event_next_signature_poll_at = 0.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(0.25)
    finally:
        webapp.control_server.stop()


@pytest.mark.parametrize("method_name", ["events_payload", "search_payload", "auto_approve_status"])
def test_session_scoped_endpoints_refresh_before_unknown_session_guard(monkeypatch, method_name):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    try:
        if method_name == "search_payload":
            payload, status = webapp.search_payload("", session="new")
        else:
            payload, status = getattr(webapp, method_name)("new")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session" if method_name != "auto_approve_status" else "target"] == "new"


def test_auto_approve_roster_uses_live_pane_working_signal(monkeypatch):
    # #28: the roster's working/idle signal comes from the LIVE pane (a cheap visible-only capture),
    # not transcript recency, while still discovering once and skipping the expensive hybrid prompt fan-out.
    info5 = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    info6 = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="6:1.0",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            )
        ],
    )
    discover_calls = []
    capture_calls = []
    pane_text = {"5": "working pane", "6": "idle pane", "6:1.0": "approval pane"}
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5", "6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (discover_calls.append(tuple(sessions)) or {"5": info5, "6": info6}, []))

    def fake_capture(session, *_args, **kwargs):
        capture_calls.append((session, kwargs.get("visible_only")))
        return pane_text.get(session, "")

    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", fake_capture)
    monkeypatch.setattr(app_module.auto_approve_tmux, "agent_screen_state", lambda text: {"key": "approval" if text == "approval pane" else "working" if text == "working pane" else "idle", "text": text})
    monkeypatch.setattr(
        app_module.auto_approve_tmux,
        "approval_prompt_state",
        lambda text: {"visible": text == "approval pane", "type": "bash" if text == "approval pane" else "", "text": "Do you want to proceed?" if text == "approval pane" else "", "yes_selected": text == "approval pane", "action": ""},
    )
    monkeypatch.setattr(app_module.auto_approve_tmux, "hybrid_approval_prompt_state", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("roster must not run the prompt-detection fan-out")))
    monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda _session: None)
    webapp = app_module.TmuxWebtermApp(["5", "6"])
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert discover_calls == [("5", "6")]  # discovered once for the whole roster, not per session
    assert {session for session, _visible in capture_calls} == {"5", "6:1.0"}
    assert all(visible_only is True for _session, visible_only in capture_calls)  # cheap visible-only capture only
    assert payload["sessions"]["5"]["screen"]["key"] == "working"  # live working pane spins
    assert payload["sessions"]["6"]["screen"]["key"] == "approval"  # pending approval lights the roster
    assert payload["sessions"]["5"]["prompt"]["visible"] is False  # no live prompt fan-out in the roster
    assert payload["sessions"]["6"]["prompt"]["visible"] is True


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


def test_transcripts_payload_returns_stale_cache_and_refreshes(monkeypatch):
    calls = []
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])

    def fake_discover(sessions):
        calls.append(len(calls) + 1)
        return {"5": info}, []

    monkeypatch.setattr(app_module, "discover_sessions", fake_discover)
    monkeypatch.setattr(app_module, "session_to_json", lambda info, cache, allow_network=False: {"session": info.session, "call": calls[-1]})
    monkeypatch.setattr(app_module, "agent_auth_status", lambda: {})
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    webapp.start_transcripts_payload_refresh = lambda: (webapp.refresh_transcripts_payload_cache() or True)
    try:
        first = webapp.transcripts_payload()
        with webapp.transcripts_payload_cache_lock:
            stored_at, value = webapp.transcripts_payload_cache
            webapp.transcripts_payload_cache = (stored_at - app_module.TRANSCRIPTS_PAYLOAD_CACHE_SECONDS - 1.0, value)
        second = webapp.transcripts_payload()
        third = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert first["sessions"]["5"]["call"] == 1
    assert second["sessions"]["5"]["call"] == 1
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert third["sessions"]["5"]["call"] == 2
    assert calls == [1, 2]


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


def test_prompt_and_screen_status_captures_discovered_agent_pane(monkeypatch):
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="6:1.0",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            )
        ],
    )
    capture_calls = []
    hybrid_targets = []

    def fake_capture(target, visible_only=False):
        capture_calls.append((target, visible_only))
        return "Do you want to proceed?\n❯ 1. Yes\n  2. No"

    def fake_hybrid(target, _visible_text, pane_text=None, **_kwargs):
        hybrid_targets.append((target, pane_text is not None))
        return {"visible": True, "type": "bash", "text": "Do you want to proceed?", "yes_selected": True, "action": "approve"}

    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", fake_capture)
    monkeypatch.setattr(app_module.auto_approve_tmux, "hybrid_approval_prompt_state", fake_hybrid)
    monkeypatch.setattr(app_module.auto_approve_tmux, "agent_screen_state", lambda _text: {"key": "approval", "text": "Do you want to proceed?"})
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6", discovered_sessions={"6": info})
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is True
    assert set(prompt) == PROMPT_STATE_KEYS
    assert screen["key"] == "approval"
    assert capture_calls == [("6:1.0", True), ("6:1.0", False)]
    assert hybrid_targets == [("6", False), ("6", True)]


def test_prompt_and_screen_status_reports_os_errors(monkeypatch):
    monkeypatch.setattr(app_module.auto_approve_tmux, "tmux_capture_pane", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tmux failed")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["error"] == "tmux failed"
    assert set(prompt) == PROMPT_STATE_KEYS | {"error"}
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
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda info, hours=24.0, **_kwargs: files_payload)

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
        localized = webapp.activity_summary_payload(locale="zh-Hant")
    finally:
        webapp.control_server.stop()

    assert calls == ["5", "5"]
    assert first["global"]["files"] == {"count": 1, "added": 1, "removed": 0}
    assert second["sessions"]["5"]["local"] == "cached test"
    assert third["sessions"]["5"]["local"] == "cached test"
    assert localized["locale"] == "zh-Hant"


def test_session_files_payload_reuses_short_cache(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None):
        calls.append((session, tuple(infos), hours, from_ref, to_ref, repo_refs))
        return {"session": session, "files": [], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda: []
    try:
        first, first_status = webapp.session_files_payload("5")
        second, second_status = webapp.session_files_payload("5")
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [("5", ("5",), 24.0, None, None, None)]
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert first["files"] == second["files"] == []
    assert first["repos"] == second["repos"] == []
    assert first["errors"] == second["errors"] == []


def test_session_files_payload_returns_stale_cache_and_refreshes(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None):
        calls.append(len(calls) + 1)
        return {"session": session, "files": [{"path": f"file-{calls[-1]}.txt"}], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda: []
    webapp.start_session_files_cache_refresh = lambda cache_key, target, *args: (target(cache_key, *args) or True)
    try:
        first, first_status = webapp.session_files_payload("5")
        key = next(iter(webapp.session_files_cache))
        with webapp.session_files_cache_lock:
            stored_at, value = webapp.session_files_cache[key]
            webapp.session_files_cache[key] = (stored_at - app_module.SESSION_FILES_CACHE_SECONDS - 1.0, value)
        second, second_status = webapp.session_files_payload("5")
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["files"] == [{"path": "file-1.txt"}]
    assert second["files"] == [{"path": "file-1.txt"}]
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert calls == [1, 2]


def test_update_client_watch_roots_filters_and_expires(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 100.0)
    try:
        payload = webapp.update_client_watch_roots({"roots": ["/repo", "relative", "", "/repo"], "files": ["/repo/DOIT.51.md", "relative"]})
        assert payload["roots"] == ["/repo"]
        assert payload["files"] == ["/repo/DOIT.51.md"]
        assert webapp.client_watch_roots_snapshot() == ["/repo"]
        assert webapp.client_watch_files_snapshot() == ["/repo/DOIT.51.md"]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: 1000.0)
        assert webapp.client_watch_roots_snapshot() == []
        assert webapp.client_watch_files_snapshot() == []
    finally:
        webapp.control_server.stop()


def test_filesystem_change_summary_counts_entry_changes():
    previous = (
        (
            "/repo",
            (
                "/repo",
                "dir",
                100,
                0,
                (
                    ("old.txt", "file", 100, 10),
                    ("same.txt", "file", 100, 10),
                    ("old-dir", "dir", 100, 0),
                    ("mod.txt", "file", 100, 10),
                ),
            ),
        ),
    )
    current = (
        (
            "/repo",
            (
                "/repo",
                "dir",
                200,
                0,
                (
                    ("new.txt", "file", 100, 10),
                    ("same.txt", "file", 100, 10),
                    ("new-dir", "dir", 100, 0),
                    ("mod.txt", "file", 200, 10),
                ),
            ),
        ),
        ("/new-root", ("/new-root", "missing")),
    )

    summary = app_module.filesystem_change_summary(previous, current)

    assert summary["roots_changed"] == 2
    assert summary["roots_added"] == 1
    assert summary["roots_removed"] == 0
    assert summary["entries_added"] == 2
    assert summary["entries_removed"] == 2
    assert summary["entries_modified"] == 1
    assert summary["files_added"] == 1
    assert summary["files_removed"] == 1
    assert summary["files_modified"] == 1
    assert summary["dirs_added"] == 1
    assert summary["dirs_removed"] == 1
    assert summary["dirs_modified"] == 0


def test_poll_client_events_once_publishes_changed_signatures(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    events = []
    settings_signatures = [("settings", 1), ("settings", 2)]
    transcript_signatures = [("transcripts", 1), ("transcripts", 2)]
    filesystem_signatures = [
        (("/repo", ("/repo", "dir", 100, 0, (("old.txt", "file", 100, 10),))),),
        (("/repo", ("/repo", "dir", 200, 0, (("new.txt", "file", 100, 10),))),),
    ]
    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: settings_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: transcript_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_watch_signature", lambda sessions: filesystem_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_for_watch", lambda sessions: ["/repo"])
    monkeypatch.setattr(
        webapp,
        "filesystem_push_payload",
        lambda roots: {
            "roots": roots,
            "directories": [{"path": "/repo", "status": 200, "ok": True, "data": {"entries": []}}],
            "listing_summary": {"roots_requested": 1, "roots_listed": 1, "roots_error": 0, "entries_listed": 0, "files_listed": 0, "dirs_listed": 0},
            "compute_ms": 1.0,
        },
    )
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        webapp.set_session_files_cache(("k",), {"files": []}, HTTPStatus.OK)
        webapp.transcripts_payload_cache = (1.0, {"sessions": {}})
        assert webapp.poll_client_events_once() == []
        assert webapp.poll_client_events_once() == ["settings_changed", "transcripts_changed", "fs_changed"]
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["settings_changed", "transcripts_changed", "fs_changed"]
    fs_payload = events[-1][1]
    assert fs_payload["change_summary"]["roots_changed"] == 1
    assert fs_payload["change_summary"]["entries_added"] == 1
    assert fs_payload["change_summary"]["entries_removed"] == 1
    assert fs_payload["listing_summary"]["roots_listed"] == 1
    assert webapp.session_files_cache != {}
    assert webapp.transcripts_payload_cache is not None


def test_poll_client_file_events_once_publishes_active_file_changes(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    signatures = [
        (("/repo/DOIT.51.md", ("/repo/DOIT.51.md", "file", 100, 10)),),
        (("/repo/DOIT.51.md", ("/repo/DOIT.51.md", "file", 200, 12)),),
    ]
    monkeypatch.setattr(webapp, "files_watch_signature", lambda: signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_client_file_events_once() == []
        assert webapp.poll_client_file_events_once() == ["files_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("files_changed", {"files": [{"path": "/repo/DOIT.51.md", "signature": ("/repo/DOIT.51.md", "file", 200, 12)}], "count": 1})]


def test_filesystem_roots_for_watch_uses_client_roots_not_agent_cwd(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    watched = tmp_path / "watched"
    transcript = tmp_path / "transcripts" / "codex.jsonl"
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:1.0",
                command="codex",
                cwd=str(repo),
                status=None,
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.update_client_watch_roots({"roots": [str(watched)]})
        roots = webapp.filesystem_roots_for_watch({"5": info})
    finally:
        webapp.control_server.stop()

    assert str(watched) in roots
    assert str(repo) not in roots
    assert str(transcript.parent) not in roots


def test_context_items_reuses_transcript_tail_cache(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "Check latency"}}) + "\n", encoding="utf-8")
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
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_tail_file_lines(path, lines):
        calls.append((path, lines))
        return transcript.read_text(encoding="utf-8")

    monkeypatch.setattr(app_module, "tail_file_lines", fake_tail_file_lines)
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        first, first_status = webapp.context_items("5", 20)
        second, second_status = webapp.context_items("5", 20)
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [(transcript, app_module.MAX_TRANSCRIPT_TAIL_LINES)]
    assert first["items"] == second["items"]


def test_yoagent_session_summary_updates_from_transcript_delta(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-06-07T10:00:00Z", "payload": {"type": "user_message", "message": "Fix the YO!agent summary table"}}),
            json.dumps({"timestamp": "2026-06-07T10:00:01Z", "payload": {"type": "agent_message", "message": "Added clickable session links."}}),
        ]) + "\n",
        encoding="utf-8",
    )
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
    prompts = []

    def fake_direct_backend(backend, prompt):
        prompts.append(prompt)
        summary = "state: working\nsummary: Updating YO!agent session summaries from transcript deltas." if len(prompts) == 1 else "state: done\nsummary: Verified the rolling summary update path."
        return summary, "", {"backend": backend, "prompt_chars": len(prompt)}

    monkeypatch.setattr(app_module, "transcript_activity_is_recent", lambda *_args, **_kwargs: False)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "run_yoagent_direct_prompt_backend", fake_direct_backend)
    settings = {"backend": "codex", "invocation": "cli", "auto_refresh": True, "refresh_interval_seconds": 120}
    try:
        first = webapp.update_yoagent_session_summary("5", info, settings)
        unchanged = webapp.update_yoagent_session_summary("5", info, settings)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": "2026-06-07T10:05:00Z", "payload": {"type": "agent_message", "message": "Tests now pass."}}) + "\n")
        second = webapp.update_yoagent_session_summary("5", info, settings)
        state = app_module.read_yolomux_state().get(app_module.YOAGENT_SESSION_SUMMARIES_STATE_KEY, {})
    finally:
        webapp.control_server.stop()

    assert first["updated"] is True
    assert unchanged["updated"] is False
    assert unchanged["reason"] == "no new transcript lines"
    assert second["updated"] is True
    assert second["state"] == "done"
    assert "Fix the YO!agent summary table" in prompts[0]
    assert "Tests now pass." not in prompts[0]
    assert "Prior summary:\nUpdating YO!agent session summaries from transcript deltas." in prompts[1]
    assert "Tests now pass." in prompts[1]
    assert "Fix the YO!agent summary table" not in prompts[1]
    assert state["5"]["rolling_summary"] == "Verified the rolling summary update path."


def test_yoagent_session_summary_auto_refresh_is_disabled_by_default(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "update_yoagent_session_summary", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("auto-refresh off must not call the model")))
    try:
        result = webapp.tick_yoagent_session_summaries({"auto_refresh": False, "refresh_interval_seconds": 120})
    finally:
        webapp.control_server.stop()

    assert result == {"enabled": False, "updated": [], "skipped": []}


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
    assert "tmux session `5`" in payload["context_lines"][1]


def test_yoagent_capability_question_is_grounded_and_readonly(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "capabilities": app_module.yoagent_capabilities_payload(),
        "sessions": {},
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "Can YO!agent read, poll, monitor, notify, and send commands to tmux panes?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "can read tmux panes" in payload["answer"]
    assert "poll live session state" in payload["answer"]
    assert "notify when configured transitions" in payload["answer"]
    assert "explicit admin UI paths" in payload["answer"]
    assert "does not currently have autonomous command-sending tools" in payload["answer"]
    assert any("capability: YOLOmux can read tmux panes" in line for line in payload["context_lines"])
    assert any("YO!agent chat itself does not currently have autonomous command-sending tools" in line for line in payload["context_lines"])


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


def test_resolve_yoagent_backend_auto_prefers_codex_then_claude(monkeypatch):
    # #41: auto resolves to codex first, then claude, then deterministic — only for installed AND
    # logged-in agents. Explicit choices pass through untouched.
    def status(claude_in, codex_in):
        return lambda *a, **k: {
            "claude": {"installed": True, "logged_in": claude_in},
            "codex": {"installed": True, "logged_in": codex_in},
        }

    monkeypatch.setattr(app_module, "agent_auth_status", status(True, True))
    assert app_module.resolve_yoagent_backend("auto") == "codex"
    monkeypatch.setattr(app_module, "agent_auth_status", status(True, False))
    assert app_module.resolve_yoagent_backend("auto") == "claude"
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, False))
    assert app_module.resolve_yoagent_backend("auto") == "deterministic"
    # an installed-but-logged-out codex is skipped in favor of a logged-in claude
    monkeypatch.setattr(app_module, "agent_auth_status", status(True, False))
    assert app_module.resolve_yoagent_backend("auto") == "claude"
    # explicit selections are never auto-resolved
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, False))
    assert app_module.resolve_yoagent_backend("claude") == "claude"
    assert app_module.resolve_yoagent_backend("deterministic") == "deterministic"


def test_yoagent_language_directive_only_for_non_english_locales():
    # DOIT.8 Phase 1: a non-English UI locale asks the LLM to answer in that language.
    assert app_module.yoagent_language_directive("zh-Hant") == "\n\n請用繁體中文回答。"
    assert app_module.yoagent_language_directive("zh-Hans") == "\n\n请用简体中文回答。"
    assert app_module.yoagent_language_directive("es") == "\n\nResponde en español."
    assert app_module.yoagent_language_directive("en") == ""
    assert app_module.yoagent_language_directive("en-XA") == ""
    assert app_module.yoagent_language_directive("system") == ""
    assert app_module.yoagent_language_directive("") == ""


def test_yoagent_chat_appends_language_directive_to_the_llm_prompt(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": True},
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "auto", "invocation": "cli"})
    captured = {}

    def fake_codex(prompt, session_id="", resume=False):
        captured["prompt"] = prompt
        return ("respuesta", "", "s1")
    monkeypatch.setattr(webapp, "run_yoagent_codex_cli", fake_codex)
    try:
        payload, status = webapp.yoagent_chat({"message": "estado?", "locale": "zh-Hant"})
    finally:
        webapp.control_server.stop()
    assert status == HTTPStatus.OK
    assert "You are YO!agent" in captured["prompt"]
    assert "請用繁體中文回答。" in captured["prompt"]


def test_yoagent_chat_auto_runs_logged_in_agent(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": True},
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "auto", "invocation": "cli"})
    monkeypatch.setattr(webapp, "run_yoagent_codex_cli", lambda prompt, session_id="", resume=False: ("codex answer", "", "codex-session-1"))
    try:
        payload, status = webapp.yoagent_chat({"message": "status?"})
    finally:
        webapp.control_server.stop()
    assert payload["backend"] == "auto"
    assert payload["backend_used"] == "codex"
    assert payload["answer"] == "codex answer"


def test_yoagent_permission_block_answer_is_preserved(monkeypatch):
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

    assert answer == "I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl."
    assert reason == ""
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
    # `codex exec resume` rejects --sandbox/--cd (it restores the original session's cwd + sandbox), so
    # the resume call must NOT pass them — passing them raised "unexpected argument '--sandbox'".
    assert "--sandbox" not in calls[1]
    assert "--cd" not in calls[1]


def test_watched_prs_payload_shapes_result_and_logs_truncation_once(monkeypatch):
    # DOIT.29: watched_prs_payload returns {watched_prs, truncated, invalid}.
    # DOIT.34 #4: the cap is logged only when the capped state CHANGES — not on every poll.
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    truncated_box = {"n": 3}
    monkeypatch.setattr(
        app_module,
        "watched_pr_metadata",
        lambda refs, cache, allow_network=True: {
            "watched_prs": [{"ref": "o/r#1", "url": "u", "number": 1, "status_label": "open"}],
            "truncated": truncated_box["n"],
            "invalid": ["bad"],
        },
    )
    events = []
    monkeypatch.setattr(webapp, "log_event", lambda *a, **k: events.append(a))

    payload = webapp.watched_prs_payload(allow_network=False)
    assert payload["watched_prs"][0]["ref"] == "o/r#1"
    assert payload["truncated"] == 3
    assert payload["invalid"] == ["bad"]
    assert "refresh_ms" not in payload
    truncation_events = lambda: [a for a in events if "watched_pr_truncated" in str(a)]
    assert len(truncation_events()) == 1, "logs the truncation on first cap"

    # A second poll with the SAME capped state does NOT log again.
    webapp.watched_prs_payload(allow_network=False)
    assert len(truncation_events()) == 1, "does not re-log an unchanged capped state every poll"

    # A changed truncation count logs a new event.
    truncated_box["n"] = 5
    webapp.watched_prs_payload(allow_network=False)
    assert len(truncation_events()) == 2, "a changed capped state logs again"

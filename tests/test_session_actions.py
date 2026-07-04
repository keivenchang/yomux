from __future__ import annotations

import os
import threading
from http import HTTPStatus



import yolomux_lib.app as app_module
import yolomux_lib.sessions as sessions_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.app import tmux_session_name_error
from yolomux_lib.app import tmux_session_name_sanitize
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo


class FakeTmuxResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_app(sessions: list[str]) -> TmuxWebtermApp:
    app = object.__new__(TmuxWebtermApp)
    app.sessions = sessions
    app.auto_worker_records = {}
    app.auto_workers_lock = threading.RLock()
    app.share_tokens = {}
    app.share_tokens_lock = threading.RLock()
    app.refresh_sessions = lambda *args, **kwargs: []
    app.set_persisted_auto_session = lambda _session, _enabled: None
    def log_event(
        session,
        event_type,
        message,
        details=None,
        *,
        message_key="",
        message_params=None,
    ):
        return {
            "session": session,
            "event_type": event_type,
            "message": message,
            "details": details or {},
            "message_key": message_key,
            "message_params": message_params or {},
        }

    app.log_event = log_event
    return app


def test_tmux_session_name_validation_is_url_and_tmux_safe():
    assert tmux_session_name_error("agent-1.ok") is None
    assert tmux_session_name_error("dynamo 2") is None
    assert tmux_session_name_error("") == "session name is required"
    assert "64" in tmux_session_name_error("x" * 65)
    assert "letters" in tmux_session_name_error("bad/name")
    assert "letters" in tmux_session_name_error("bad:name")
    assert "letters" in tmux_session_name_error("bad,name")


def test_tmux_session_name_sanitize_mirrors_tmux_dot_and_colon_rewrite():
    # tmux rewrites "." and ":" to "_"; everything else (dash, underscore, space) is preserved.
    assert tmux_session_name_sanitize("dynamo-utils.dev") == "dynamo-utils_dev"
    assert tmux_session_name_sanitize("a.b:c.d") == "a_b_c_d"
    assert tmux_session_name_sanitize("  spaced name  ") == "spaced name"
    assert tmux_session_name_sanitize("plain-name_2") == "plain-name_2"


def test_rename_session_calls_tmux_and_updates_session_order(monkeypatch):
    app = make_app(["1", "ant"])
    calls = []

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        if args[0] == "rename-session":
            app.sessions = ["agent", "ant"]
        return FakeTmuxResult()

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.rename_session("1", "agent")

    assert status == HTTPStatus.OK
    assert payload["renamed"] is True
    assert payload["new_session"] == "agent"
    assert payload["sessions"] == ["agent", "ant"]
    assert calls == [["rename-session", "-t", "1:", "agent"]]


def test_rename_session_rejects_duplicate_and_invalid_names(monkeypatch):
    app = make_app(["1", "ant"])
    calls = []
    monkeypatch.setattr(app_module, "tmux", lambda args, timeout=5.0: calls.append(args) or FakeTmuxResult())

    duplicate_payload, duplicate_status = app.rename_session("1", "ant")
    invalid_payload, invalid_status = app.rename_session("1", "bad/name")

    assert duplicate_status == HTTPStatus.CONFLICT
    assert "already exists" in duplicate_payload["error"]
    assert invalid_status == HTTPStatus.BAD_REQUEST
    assert "letters" in invalid_payload["error"]
    assert calls == []


def test_rename_session_sanitizes_dot_to_match_tmux_stored_name(monkeypatch):
    app = make_app(["1", "ant"])
    calls = []

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        if args[0] == "rename-session":
            # tmux stores the sanitized name; the app must report and switch to that, not the dotted input.
            app.sessions = ["dynamo-utils_dev", "ant"]
        return FakeTmuxResult()

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.rename_session("1", "dynamo-utils.dev")

    assert status == HTTPStatus.OK
    assert payload["renamed"] is True
    assert payload["new_session"] == "dynamo-utils_dev"
    assert calls == [["rename-session", "-t", "1:", "dynamo-utils_dev"]]


def test_rename_session_detects_collision_after_sanitizing(monkeypatch):
    app = make_app(["1", "dynamo-utils_dev"])
    calls = []
    monkeypatch.setattr(app_module, "tmux", lambda args, timeout=5.0: calls.append(args) or FakeTmuxResult())

    payload, status = app.rename_session("1", "dynamo-utils.dev")

    assert status == HTTPStatus.CONFLICT
    assert "already exists" in payload["error"]
    assert payload["new_name"] == "dynamo-utils_dev"
    assert calls == []


def test_kill_session_calls_tmux_and_removes_session(monkeypatch):
    app = make_app(["1", "ant"])
    calls = []

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        if args[0] == "kill-session":
            app.sessions = ["ant"]
        return FakeTmuxResult()

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.kill_session("1")

    assert status == HTTPStatus.OK
    assert payload["killed"] is True
    assert payload["sessions"] == ["ant"]
    assert calls == [["kill-session", "-t", "1:"]]


def test_tmux_select_window_calls_direct_target(monkeypatch):
    app = make_app(["1"])
    calls = []
    monkeypatch.setattr(app_module, "tmux_session_client_rows", lambda session: [
        {"name": "/dev/pts/1", "session": session, "width": 100, "flags": "attached,UTF-8"},
        {"name": "client-browser", "session": session, "width": 80, "flags": "attached,ignore-size,UTF-8"},
        {"name": "", "session": session, "width": 120, "flags": "attached,UTF-8"},
    ])

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        return FakeTmuxResult()

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.tmux_select_window("1", "3")
    invalid_payload, invalid_status = app.tmux_select_window("1", "bad")

    assert status == HTTPStatus.OK
    assert payload == {"session": "1", "window": "3", "ok": True}
    assert invalid_status == HTTPStatus.BAD_REQUEST
    assert "window" in invalid_payload["error"]
    assert calls == [
        ["select-window", "-t", "1:3"],
        ["switch-client", "-c", "/dev/pts/1", "-t", "1:3"],
        ["switch-client", "-c", "client-browser", "-t", "1:3"],
    ]


def test_tmux_select_window_keeps_click_success_when_client_switch_fails(monkeypatch):
    app = make_app(["1"])
    calls = []
    monkeypatch.setattr(app_module, "tmux_session_client_rows", lambda session: [
        {"name": "/dev/pts/1", "session": session, "width": 100, "flags": "attached,UTF-8"},
    ])

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        if args[0] == "switch-client":
            return FakeTmuxResult(returncode=1, stderr="client vanished")
        return FakeTmuxResult()

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.tmux_select_window("1", "2")

    assert status == HTTPStatus.OK
    assert payload == {"session": "1", "window": "2", "ok": True}
    assert calls == [
        ["select-window", "-t", "1:2"],
        ["switch-client", "-c", "/dev/pts/1", "-t", "1:2"],
    ]


def test_list_tmux_panes_captures_window_name(monkeypatch):
    calls = []

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        return FakeTmuxResult(stdout="1\t2\tcodex\t0\t%42\t/repo\tbash\t1\t1\ttitle\t123\n")

    monkeypatch.setattr(sessions_module, "tmux", fake_tmux)

    panes, error = sessions_module.list_tmux_panes()

    assert error is None
    assert panes[0].window == "2"
    assert panes[0].window_name == "codex"
    assert "#{window_name}" in calls[0][-1]


def test_tmux_copy_selection_reads_fresh_tmux_buffer(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        pane="0",
        pane_id="%42",
        target="%42",
        current_path="/repo",
        command="bash",
        active=True,
        window_active=True,
        title="bash",
        pid=123,
    )
    app = make_app(["1"])
    calls = []
    buffer_signature = ["100:3:old", "101:11:fresh"]

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": SessionInfo("1", [pane], pane, [])}, []))

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        if args[:4] == ["display-message", "-p", "-t", "%42"] and args[4] == "#{pane_in_mode}":
            return FakeTmuxResult(stdout="1\n")
        if args[:4] == ["display-message", "-p", "-t", "%42"] and args[4] == "#{buffer_created}:#{buffer_size}:#{buffer_sample}":
            return FakeTmuxResult(stdout=f"{buffer_signature.pop(0)}\n")
        if args == ["send-keys", "-t", "%42", "-X", "copy-selection-no-clear"]:
            return FakeTmuxResult()
        if args == ["send-keys", "-t", "%42", "-X", "cancel"]:
            return FakeTmuxResult()
        if args == ["save-buffer", "-"]:
            return FakeTmuxResult(stdout="fresh text\n")
        raise AssertionError(f"unexpected tmux call: {args}")

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.tmux_copy_selection("1")

    assert status == HTTPStatus.OK
    assert payload["copied"] is True
    assert payload["text"] == "fresh text\n"
    assert payload["chars"] == len("fresh text\n")
    assert calls == [
        ["display-message", "-p", "-t", "%42", "#{pane_in_mode}"],
        ["display-message", "-p", "-t", "%42", "#{buffer_created}:#{buffer_size}:#{buffer_sample}"],
        ["send-keys", "-t", "%42", "-X", "copy-selection-no-clear"],
        ["display-message", "-p", "-t", "%42", "#{buffer_created}:#{buffer_size}:#{buffer_sample}"],
        ["save-buffer", "-"],
        ["send-keys", "-t", "%42", "-X", "cancel"],
    ]


def test_tmux_copy_selection_does_not_return_stale_buffer(monkeypatch):
    app = make_app(["1"])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    calls = []

    def fake_tmux(args, timeout=5.0):
        calls.append(args)
        if args[:3] == ["display-message", "-p", "-t"] and args[-1] == "#{pane_in_mode}":
            return FakeTmuxResult(stdout="1\n")
        if args[:3] == ["display-message", "-p", "-t"] and args[-1] == "#{buffer_created}:#{buffer_size}:#{buffer_sample}":
            return FakeTmuxResult(stdout="100:3:old\n")
        if args[:3] == ["send-keys", "-t", "1:"]:
            return FakeTmuxResult()
        if args == ["save-buffer", "-"]:
            raise AssertionError("must not read a stale buffer when copy did not create one")
        raise AssertionError(f"unexpected tmux call: {args}")

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.tmux_copy_selection("1")

    assert status == HTTPStatus.OK
    assert payload["copied"] is False
    assert payload["text"] == ""
    assert payload["error"] == "no tmux selection copied"
    assert calls[-1] == ["send-keys", "-t", "1:", "-X", "cancel"]

from __future__ import annotations

import os
from http import HTTPStatus
from types import SimpleNamespace

import pytest

import yolomux_lib.app as app_module
import yolomux_lib.sessions as sessions_module
from yolomux_lib.app import tmux_session_name_error
from yolomux_lib.app import tmux_session_name_sanitize
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo


class FakeTmuxResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def make_app(make_tmux_webterm_app):
    def factory(sessions: list[str]):
        app = make_tmux_webterm_app(sessions)
        app.approval_client = SimpleNamespace(status_session=lambda _session: [], stop_session=lambda _session: {"ok": True})
        app.refresh_sessions = lambda *args, **kwargs: []
        app.set_persisted_auto_session = lambda _session, _enabled: None
        app.log_event = lambda session, event_type, message, details=None, *, message_key="", message_params=None: {
            "session": session,
            "event_type": event_type,
            "message": message,
            "details": details or {},
            "message_key": message_key,
            "message_params": message_params or {},
        }
        return app

    return factory


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


def test_session_action_app_builder_keeps_the_full_app_contract(make_app):
    app = make_app(["1"])

    assert app.__class__.__name__ == "TmuxWebtermApp"
    assert hasattr(app, "client_events")
    assert hasattr(app, "control_server")


def test_rename_session_calls_tmux_and_updates_session_order(monkeypatch, make_app):
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


def test_rename_session_rejects_duplicate_and_invalid_names(monkeypatch, make_app):
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


def test_rename_session_sanitizes_dot_to_match_tmux_stored_name(monkeypatch, make_app):
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


def test_rename_session_detects_collision_after_sanitizing(monkeypatch, make_app):
    app = make_app(["1", "dynamo-utils_dev"])
    calls = []
    monkeypatch.setattr(app_module, "tmux", lambda args, timeout=5.0: calls.append(args) or FakeTmuxResult())

    payload, status = app.rename_session("1", "dynamo-utils.dev")

    assert status == HTTPStatus.CONFLICT
    assert "already exists" in payload["error"]
    assert payload["new_name"] == "dynamo-utils_dev"
    assert calls == []


def test_kill_session_calls_tmux_and_removes_session(monkeypatch, make_app):
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


def test_tmux_select_window_runs_exactly_one_select_and_no_client_fanout(monkeypatch, make_app):
    """select-window IS the whole job: it switches every client attached to the session
    synchronously. The retired per-client `switch-client` fan-out was a no-op by
    construction, serially delayed every switch response, and poked the user's own
    hand-attached terminals (audible bell reports) — it must never return."""
    app = make_app(["1"])
    calls = []
    monkeypatch.setattr(app_module, "tmux_session_client_rows", lambda session: [
        {"name": "/dev/pts/1", "session": session, "width": 100, "flags": "attached,UTF-8"},
        {"name": "client-browser", "session": session, "width": 80, "flags": "attached,ignore-size,UTF-8"},
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
    assert calls == [["select-window", "-t", "1:3"]]
    assert not any(args and args[0] == "switch-client" for args in calls)


def test_tmux_select_window_failure_surfaces_diagnostic(monkeypatch, make_app):
    app = make_app(["1"])

    def fake_tmux(args, timeout=5.0):
        return FakeTmuxResult(returncode=1, stderr="no such window")

    monkeypatch.setattr(app_module, "tmux", fake_tmux)

    payload, status = app.tmux_select_window("1", "9")

    assert status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "no such window" in str(payload)


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


def test_tmux_copy_selection_reads_fresh_tmux_buffer(monkeypatch, make_app):
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


def test_tmux_copy_selection_does_not_return_stale_buffer(monkeypatch, make_app):
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

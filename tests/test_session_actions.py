from __future__ import annotations

import os
import threading
from http import HTTPStatus


os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")
os.environ.setdefault("YOLOMUX_STATE_DIR", "/tmp/yolomux-test-state")

import yolomux_lib.app as app_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.app import tmux_session_name_error


class FakeTmuxResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_app(sessions: list[str]) -> TmuxWebtermApp:
    app = object.__new__(TmuxWebtermApp)
    app.sessions = sessions
    app.auto_workers = {}
    app.auto_workers_lock = threading.RLock()
    app.refresh_sessions = lambda: []
    app.set_persisted_auto_session = lambda _session, _enabled: None
    app.log_event = lambda session, event_type, message, details=None: {
        "session": session,
        "event_type": event_type,
        "message": message,
        "details": details or {},
    }
    return app


def test_tmux_session_name_validation_is_url_and_tmux_safe():
    assert tmux_session_name_error("agent-1.ok") is None
    assert tmux_session_name_error("dynamo 2") is None
    assert tmux_session_name_error("") == "session name is required"
    assert "64" in tmux_session_name_error("x" * 65)
    assert "letters" in tmux_session_name_error("bad/name")
    assert "letters" in tmux_session_name_error("bad:name")
    assert "letters" in tmux_session_name_error("bad,name")


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

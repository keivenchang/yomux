"""Tests for the launcher's authenticated session probe."""

from __future__ import annotations

import argparse

from tools import launcher_probe


def test_probe_sessions_accepts_every_expected_session(monkeypatch, capsys):
    calls = []

    def get_json(scheme, host, port, path, headers, *, timeout):
        calls.append((scheme, host, port, path, headers, timeout))
        return {"exists": True}

    monkeypatch.setattr(launcher_probe, "_get_json", get_json)
    args = argparse.Namespace(scheme="http", host="127.0.0.1", port=7110, sessions="1,2", timeout=1.0)

    assert launcher_probe._probe_sessions(args) == 0
    assert "all 2 visible" in capsys.readouterr().out
    assert [call[3] for call in calls] == ["/api/tmux-session-exists?session=1", "/api/tmux-session-exists?session=2"]


def test_probe_sessions_reports_missing_session(monkeypatch, capsys):
    monkeypatch.setattr(launcher_probe, "_get_json", lambda *_args, **_kwargs: {"exists": False})
    monkeypatch.setattr(launcher_probe.time, "sleep", lambda _seconds: None)
    args = argparse.Namespace(scheme="http", host="127.0.0.1", port=7110, sessions="missing", timeout=0.0)

    assert launcher_probe._probe_sessions(args) == 1
    assert "incomplete" in capsys.readouterr().err

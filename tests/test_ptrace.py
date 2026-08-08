from __future__ import annotations

from yolomux_lib import cli
from yolomux_lib import ptrace


def test_diagnostic_ptrace_is_off_without_dang(monkeypatch, capsys):
    monkeypatch.setattr(cli, "allow_diagnostic_ptrace", lambda: (_ for _ in ()).throw(AssertionError("normal launch must not opt in")))

    assert cli.configure_dang_diagnostic_ptrace(False) is False
    assert capsys.readouterr().out == ""


def test_dang_diagnostic_ptrace_reports_enabled(monkeypatch, capsys):
    logs = []
    monkeypatch.setattr(cli, "allow_diagnostic_ptrace", lambda: True)
    monkeypatch.setattr(cli, "emit_server_log", lambda *args, **kwargs: logs.append((args, kwargs)))

    assert cli.configure_dang_diagnostic_ptrace(True) is True
    assert "PR_SET_PTRACER_ANY" in capsys.readouterr().out
    assert logs == [(("info", "server", "Development ptrace diagnostics are enabled by --dang (PR_SET_PTRACER_ANY)."), {"category": "diagnostics"})]


def test_dang_diagnostic_ptrace_fails_soft(monkeypatch, capsys):
    logs = []
    monkeypatch.setattr(cli, "allow_diagnostic_ptrace", lambda: False)
    monkeypatch.setattr(cli, "emit_server_log", lambda *args, **kwargs: logs.append((args, kwargs)))

    assert cli.configure_dang_diagnostic_ptrace(True) is False
    assert "continuing without diagnostic attach" in capsys.readouterr().out
    assert logs == [(("warning", "server", "Development ptrace diagnostics are unavailable; continuing without diagnostic attach."), {"category": "diagnostics"})]


def test_allow_diagnostic_ptrace_is_best_effort_when_libc_is_missing(monkeypatch):
    monkeypatch.setattr(ptrace, "_LIBC", None)

    assert ptrace.allow_diagnostic_ptrace() is False

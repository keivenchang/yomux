import logging
import os
import subprocess

from yolomux_lib import common
from yolomux_lib import workdir
from yolomux_lib.workdir import agent_auth_status
from yolomux_lib.workdir import agent_command
from yolomux_lib.workdir import available_agent_commands


def test_agent_command_uses_plain_agent_cli_unless_dangerously_yolo():
    assert agent_command("claude", dangerously_yolo=False) == "claude"
    assert agent_command("codex", dangerously_yolo=False) == "codex"
    assert agent_command("claude", dangerously_yolo=True) == "claude --dangerously-skip-permissions --bare"
    assert agent_command("codex", dangerously_yolo=True) == "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"


def _fake_run(results: dict[str, subprocess.CompletedProcess]):
    def run(cmd, *args, **kwargs):
        agent = cmd[0]
        if agent in results:
            return results[agent]
        raise OSError("not found")
    return run


def _completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def test_agent_auth_status_parses_claude_json_and_codex_text(monkeypatch):
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(workdir.subprocess, "run", _fake_run({
        "claude": _completed(["claude"], 0, stdout='{"loggedIn": true, "email": "a@b.c"}'),
        "codex": _completed(["codex"], 0, stdout="Logged in using ChatGPT"),
    }))
    status = agent_auth_status(force=True)
    assert status["claude"] == {"installed": True, "logged_in": True}
    assert status["codex"] == {"installed": True, "logged_in": True}


def test_agent_auth_status_treats_logged_out_and_missing_as_not_logged_in(monkeypatch):
    # claude installed but JSON says loggedIn=false; codex installed but prints a logged-out marker;
    # a missing binary is installed=False, logged_in=False.
    monkeypatch.setattr(workdir.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else ("/usr/bin/codex" if name == "codex" else None))
    monkeypatch.setattr(workdir.subprocess, "run", _fake_run({
        "claude": _completed(["claude"], 0, stdout='{"loggedIn": false}'),
        "codex": _completed(["codex"], 0, stdout="Not logged in"),
    }))
    status = agent_auth_status(force=True)
    assert status["claude"] == {"installed": True, "logged_in": False}
    assert status["codex"] == {"installed": True, "logged_in": False}


def test_server_path_self_heal_finds_home_local_bin_agent(monkeypatch, tmp_path):
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    claude = local_bin / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    claude.chmod(0o700)
    bare_bin = tmp_path / "bin"
    bare_bin.mkdir()
    monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PATH", str(bare_bin))

    assert workdir.shutil.which("claude") is None
    agents = available_agent_commands()
    assert agents[:1] == ["claude"]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(local_bin)


def test_agent_auth_status_marks_missing_cli_as_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(common.Path, "home", lambda: tmp_path)

    status = agent_auth_status(force=True)

    assert status["claude"] == {"installed": False, "logged_in": False, "unavailable_reason": "not-on-path"}
    assert status["codex"] == {"installed": False, "logged_in": False, "unavailable_reason": "not-on-path"}


def test_missing_agent_path_warning_is_one_shot(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
    common._AGENT_PATH_WARNING_KEYS.clear()

    with caplog.at_level(logging.WARNING):
        common.warn_unavailable_agent_commands_once(("claude",))
        common.warn_unavailable_agent_commands_once(("claude",))

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "claude not found on server PATH=" in messages[0]


def test_missing_agent_path_warning_skips_resolvable_agent(monkeypatch, tmp_path, caplog):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    claude.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
    common._AGENT_PATH_WARNING_KEYS.clear()

    with caplog.at_level(logging.WARNING):
        common.warn_unavailable_agent_commands_once(("claude",))

    assert [record.getMessage() for record in caplog.records] == []


def test_agent_auth_status_nonzero_exit_and_timeout_are_not_logged_in(monkeypatch):
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(cmd, *args, **kwargs):
        if cmd[0] == "claude":
            return _completed(["claude"], 1, stderr="error")
        raise subprocess.TimeoutExpired(cmd, 4.0)
    monkeypatch.setattr(workdir.subprocess, "run", run)
    status = agent_auth_status(force=True)
    assert status["claude"]["logged_in"] is False
    assert status["codex"]["logged_in"] is False


def test_agent_auth_status_caches_until_forced(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(cmd, *args, **kwargs):
        calls["n"] += 1
        return _completed(cmd, 0, stdout='{"loggedIn": true}' if cmd[0] == "claude" else "Logged in")
    monkeypatch.setattr(workdir.subprocess, "run", run)
    agent_auth_status(force=True)
    first = calls["n"]
    agent_auth_status()  # cached — no new subprocess calls
    assert calls["n"] == first
    agent_auth_status(force=True)  # forced — probes again
    assert calls["n"] > first

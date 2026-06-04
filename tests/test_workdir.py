import subprocess

from yolomux_lib import workdir
from yolomux_lib.workdir import agent_auth_status
from yolomux_lib.workdir import agent_command


def test_agent_command_uses_plain_agent_cli_unless_dangerously_yolo():
    assert agent_command("claude", dangerously_yolo=False) == "claude"
    assert agent_command("codex", dangerously_yolo=False) == "codex"
    assert agent_command("claude", dangerously_yolo=True) == "claude --dangerously-skip-permissions"
    assert agent_command("codex", dangerously_yolo=True) == "codex --dangerously-bypass-approvals-and-sandbox"


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

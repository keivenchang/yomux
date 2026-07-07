import logging
import os
import subprocess

import pytest

from yolomux_lib import common
from yolomux_lib import workdir
from yolomux_lib.agent_comms import codex_app_server
from yolomux_lib.workdir import agent_auth_status
from yolomux_lib.workdir import agent_command
from yolomux_lib.workdir import available_agent_commands
from yolomux_lib.workdir import available_terminal_commands
from yolomux_lib.workdir import terminal_command


@pytest.fixture(autouse=True)
def clear_agent_auth_status_cache():
    workdir._clear_agent_auth_status_cache_for_tests()
    yield
    workdir._clear_agent_auth_status_cache_for_tests()


def test_agent_command_uses_plain_agent_cli_unless_dangerously_yolo():
    assert agent_command("claude", dangerously_yolo=False) == "claude"
    assert agent_command("codex", dangerously_yolo=False) == "codex"
    assert agent_command("claude", dangerously_yolo=True) == "claude --dangerously-skip-permissions"
    assert agent_command("codex", dangerously_yolo=True) == "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"


def test_terminal_commands_are_discovered_and_selected_only_by_name(monkeypatch, tmp_path):
    bash = tmp_path / "bash"
    tsh = tmp_path / "tsh"
    zsh = tmp_path / "zsh"
    for command in (bash, tsh, zsh):
        command.touch()
        command.chmod(0o755)
    shell_list = tmp_path / "shells"
    shell_list.write_text(f"{bash}\n{tsh}\n", encoding="utf-8")
    monkeypatch.setattr(workdir, "SYSTEM_SHELLS_PATH", shell_list)
    monkeypatch.setattr(workdir, "TERMINAL_COMMAND_CANDIDATES", ("bash", "tsh", "zsh", "tmux"))
    monkeypatch.setattr(workdir.shutil, "which", lambda name: {"bash": str(bash), "tsh": str(tsh), "zsh": str(zsh)}.get(name))

    assert available_terminal_commands() == ["bash", "tsh", "zsh"]
    assert terminal_command("tsh") == str(tsh)
    assert terminal_command("../../bin/sh") is None


def test_numeric_session_workdir_uses_matching_yolomux_dev_checkout(monkeypatch, tmp_path):
    dev_checkout = tmp_path / "yolomux.dev8002"
    dev_checkout.mkdir()
    monkeypatch.setattr(workdir.Path, "home", lambda: tmp_path)

    assert workdir.session_workdir("8002") == dev_checkout
    assert workdir.numbered_session_workdir("8002") == dev_checkout


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


def test_codex_runtime_env_defaults_to_user_codex_home(monkeypatch, tmp_path):
    monkeypatch.delenv("YOLOMUX_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(common.Path, "home", lambda: tmp_path)

    env = common.codex_runtime_env({"PATH": "/usr/bin"})

    assert env["CODEX_HOME"] == str(tmp_path / ".codex")
    assert env["TERM"] == "xterm-256color"
    assert env["NO_COLOR"] == "1"


def test_codex_runtime_env_respects_yolomux_codex_home_override(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))

    env = common.codex_runtime_env({"PATH": "/usr/bin"})

    assert env["CODEX_HOME"] == str(codex_home)
    assert codex_home.is_dir()


@pytest.mark.parametrize(
    ("path", "extra", "with_local_bin", "expected_suffixes"),
    [
        ("", "", False, []),
        ("/usr/bin", "/opt/agents:/opt/agents", False, ["/opt/agents", "/usr/bin"]),
        ("/opt/agents:/usr/bin", "/opt/agents:/opt/other", False, ["/opt/other", "/opt/agents", "/usr/bin"]),
        ("/usr/bin", "", True, [".local/bin", "/usr/bin"]),
        ("/usr/bin", "~/agents:relative", False, ["agents", "relative", "/usr/bin"]),
    ],
)
def test_server_and_codex_app_server_share_runtime_path_order(monkeypatch, tmp_path, path, extra, with_local_bin, expected_suffixes):
    if with_local_bin:
        (tmp_path / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(codex_app_server.Path, "home", lambda: tmp_path)
    base_env = {
        "PATH": path,
        "YOLOMUX_EXTRA_PATH": extra,
        "YOLOMUX_CODEX_HOME": str(tmp_path / "codex-home"),
    }

    server_env = common.codex_runtime_env(base_env)
    app_server_env = codex_app_server.codex_runtime_env(base_env)

    assert server_env["PATH"] == app_server_env["PATH"]
    entries = server_env["PATH"].split(os.pathsep) if server_env["PATH"] else []
    expected = []
    for suffix in expected_suffixes:
        if suffix == ".local/bin":
            expected.append(str(tmp_path / suffix))
        elif suffix == "agents":
            expected.append(str(tmp_path / suffix))
        else:
            expected.append(suffix)
    assert entries == expected


def test_agent_auth_status_probes_codex_with_runtime_env(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    seen: dict[str, dict[str, str] | None] = {}
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(cmd, *args, **kwargs):
        seen[cmd[0]] = kwargs.get("env")
        if cmd[0] == "claude":
            return _completed(cmd, 0, stdout='{"loggedIn": true}')
        return _completed(cmd, 0, stdout="Logged in using ChatGPT")

    monkeypatch.setattr(workdir.subprocess, "run", run)
    status = agent_auth_status(force=True)

    assert status["codex"]["logged_in"] is True
    assert seen["claude"] is None
    assert seen["codex"]["CODEX_HOME"] == str(codex_home)


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


def test_agent_auth_status_nonzero_exit_and_timeout_are_unknown(monkeypatch):
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(cmd, *args, **kwargs):
        if cmd[0] == "claude":
            return _completed(["claude"], 1, stderr="error")
        raise subprocess.TimeoutExpired(cmd, 4.0)
    monkeypatch.setattr(workdir.subprocess, "run", run)
    status = agent_auth_status(force=True)
    assert status["claude"]["logged_in"] is None
    assert status["claude"]["unavailable_reason"] == "auth-unknown"
    assert status["codex"]["logged_in"] is None
    assert status["codex"]["unavailable_reason"] == "auth-unknown"


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


def test_agent_auth_status_nonblocking_cold_returns_unknown_and_schedules_refresh(monkeypatch):
    starts = []
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(workdir, "start_agent_auth_status_refresh", lambda force=False: starts.append(force) or True)
    monkeypatch.setattr(workdir.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cold snapshot must not probe")))

    status = agent_auth_status(block=False, allow_stale=True, refresh=True)

    assert starts == [True]
    assert status["claude"] == {"installed": True, "logged_in": None, "unavailable_reason": "auth-unknown"}
    assert status["codex"] == {"installed": True, "logged_in": None, "unavailable_reason": "auth-unknown"}


def test_agent_auth_background_refresh_preserves_previous_known_state_on_transient_unknown(monkeypatch):
    monkeypatch.setattr(workdir.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(workdir.subprocess, "run", _fake_run({
        "claude": _completed(["claude"], 0, stdout='{"loggedIn": true}'),
        "codex": _completed(["codex"], 0, stdout="Logged in using ChatGPT"),
    }))

    assert agent_auth_status(force=True)["codex"]["logged_in"] is True

    def timeout_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 4.0)

    monkeypatch.setattr(workdir.subprocess, "run", timeout_run)
    refreshed = workdir._refresh_agent_auth_status(preserve_previous_known=True)

    assert refreshed["claude"] == {"installed": True, "logged_in": True}
    assert refreshed["codex"] == {"installed": True, "logged_in": True}
    forced = agent_auth_status(force=True)
    assert forced["claude"]["logged_in"] is None
    assert forced["claude"]["unavailable_reason"] == "auth-unknown"

"""Classification tests for real-client capability probes."""

from __future__ import annotations

from pathlib import Path
import subprocess

from tests.agent_capabilities import probe_client


def fake_runner(*expected: tuple[str, ...]):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 5
        assert command[-1] == "--version"
        return subprocess.CompletedProcess(command, 0, "client 1.2.3\n", "")

    run.calls = calls
    return run


def test_missing_executable_reports_host_and_docker_skip_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.agent_capabilities.shutil.which", lambda *_args, **_kwargs: None)

    capability = probe_client(
        "claude", environ={"PATH": str(tmp_path)}, home=tmp_path,
        docker_image="yolomux-test:fixture", runner=fake_runner(),
    )

    assert capability.version is None
    assert capability.executable_path is None
    assert not capability.can_run_host
    assert not capability.docker_safe
    assert capability.skip_reasons == (
        "host: executable 'claude' is not on PATH",
        "host: no local config/auth environment detected",
        "docker: host executable cannot be mounted",
    )


def test_auth_is_classified_without_exposing_secret(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.agent_capabilities.shutil.which", lambda *_args, **_kwargs: "/bin/claude")
    runner = fake_runner()

    capability = probe_client(
        "claude", environ={"PATH": "/bin", "ANTHROPIC_API_KEY": "secret"}, home=tmp_path,
        docker_available=False, runner=runner,
    )

    assert capability.can_run_host
    assert capability.present_env == ("ANTHROPIC_API_KEY",)
    assert all("secret" not in reason for reason in capability.skip_reasons)
    assert capability.skip_reasons == ("docker: Docker daemon or CLI unavailable",)
    assert runner.calls == [["/bin/claude", "--version"]]


def test_docker_probe_is_network_isolated_and_reports_image_blocker(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.agent_capabilities.shutil.which", lambda *_args, **_kwargs: "/bin/codex")
    runner = fake_runner()

    capability = probe_client(
        "codex", environ={"PATH": "/bin", "CODEX_API_KEY": "present"}, home=tmp_path,
        docker_image="yolomux-test:fixture", runner=runner,
    )

    assert capability.docker_safe
    assert runner.calls == [
        ["/bin/codex", "--version"],
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "codex", "yolomux-test:fixture", "--version"],
    ]


def test_missing_config_and_auth_has_actionable_skip_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.agent_capabilities.shutil.which", lambda *_args, **_kwargs: "/bin/opencode")

    capability = probe_client(
        "opencode", environ={"PATH": "/bin"}, home=tmp_path,
        docker_available=False, runner=fake_runner(),
    )

    assert not capability.can_run_host
    assert "host: no local config/auth environment detected" in capability.skip_reasons
    assert capability.required_env == ("OPENCODE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def test_capability_probe_never_forwards_auth_values_to_docker(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.agent_capabilities.shutil.which", lambda *_args, **_kwargs: "/bin/claude")
    runner = fake_runner()

    probe_client(
        "claude", environ={"PATH": "/bin", "ANTHROPIC_API_KEY": "secret"}, home=tmp_path,
        docker_image="yolomux-test:fixture", runner=runner,
    )

    docker_command = runner.calls[1]
    assert "secret" not in docker_command
    assert "--network" in docker_command
    assert docker_command[docker_command.index("--network") + 1] == "none"

"""Read-only capability probes for installed agent CLIs.

The only client invocation made by this module is ``--version``. Docker probes use
``--network none`` and therefore cannot contact an upstream service.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Mapping, Sequence


CLIENTS = ("claude", "codex", "opencode")
VERSION_ARGS = ("--version",)


@dataclass(frozen=True)
class ClientSpec:
    name: str
    executable: str
    config_paths: tuple[str, ...]
    required_env: tuple[str, ...]
    auth_env: tuple[str, ...]


@dataclass(frozen=True)
class ClientCapability:
    name: str
    version: str | None
    executable_path: str | None
    config_paths: tuple[str, ...]
    present_config_paths: tuple[str, ...]
    required_env: tuple[str, ...]
    present_env: tuple[str, ...]
    docker_safe: bool
    skip_reasons: tuple[str, ...]

    @property
    def can_run_host(self) -> bool:
        return not any(reason.startswith("host:") for reason in self.skip_reasons)


SPECS: dict[str, ClientSpec] = {
    "claude": ClientSpec(
        "claude", "claude", ("~/.claude",), (), ("ANTHROPIC_API_KEY",),
    ),
    "codex": ClientSpec(
        "codex", "codex", ("~/.codex",), (), ("OPENAI_API_KEY", "CODEX_API_KEY"),
    ),
    "opencode": ClientSpec(
        "opencode", "opencode", ("~/.config/opencode", "~/.local/share/opencode"), (),
        ("OPENCODE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
    ),
}

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_version(command: Sequence[str], runner: Runner) -> tuple[str | None, str | None]:
    try:
        result = runner(
            list(command) + list(VERSION_ARGS),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    output = (result.stdout or result.stderr).strip()
    return (output.splitlines()[0] if output else None), None if result.returncode == 0 else output


def probe_client(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    docker_image: str | None = None,
    docker_available: bool = True,
    runner: Runner = subprocess.run,
) -> ClientCapability:
    """Probe one client without starting a session or making network requests."""

    spec = SPECS[name]
    env = os.environ if environ is None else environ
    home_path = Path.home() if home is None else home
    executable_path = shutil.which(spec.executable, path=env.get("PATH"))
    version = None
    reasons: list[str] = []
    if executable_path is None:
        reasons.append(f"host: executable {spec.executable!r} is not on PATH")
    else:
        version, error = _run_version([executable_path], runner)
        if error:
            reasons.append(f"host: --version failed: {error}")

    present_config = tuple(
        path for path in spec.config_paths
        if (home_path / path.removeprefix("~/")).exists()
    )
    present_env = tuple(key for key in spec.auth_env if env.get(key))
    if not present_config and not present_env:
        reasons.append("host: no local config/auth environment detected")

    docker_safe = False
    if not docker_available:
        reasons.append("docker: Docker daemon or CLI unavailable")
    elif not docker_image:
        reasons.append("docker: test image is not available")
    elif executable_path is None:
        reasons.append("docker: host executable cannot be mounted")
    else:
        docker_version, error = _run_version(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", spec.executable, docker_image],
            runner,
        )
        docker_safe = error is None and docker_version is not None
        if not docker_safe:
            reasons.append(f"docker: isolated --version probe failed: {error or 'no output'}")

    return ClientCapability(
        name=name,
        version=version,
        executable_path=executable_path,
        config_paths=spec.config_paths,
        present_config_paths=present_config,
        required_env=spec.required_env + spec.auth_env,
        present_env=present_env,
        docker_safe=docker_safe,
        skip_reasons=tuple(reasons),
    )


def probe_installed_clients(**kwargs: object) -> dict[str, ClientCapability]:
    return {name: probe_client(name, **kwargs) for name in CLIENTS}

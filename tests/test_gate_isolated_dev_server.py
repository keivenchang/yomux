# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""M13: an isolated dev server actually runs, and every clause of "isolated" is checked.

This is the harness the restart-sequence, statsd-self-recovery and teardown-isolation acceptance
work runs against. It is asserted here rather than assumed there, because "isolated" was
previously a property nobody had measured: a harness that quietly inherited the operator's config
directory, or bound a port the operator was using, would still pass every test written on top of
it while corrupting the machine it ran on.

The isolation clauses proven below, each against the RUNNING process rather than the dict that
was handed to `Popen`:

  * the port is ephemeral and is not one of the operator's live servers,
  * the tmux socket is the fixture's private one, not the default server,
  * config, state, cache, runtime, HOME and the auth config all resolve inside the fixture root,
  * the server writes into that root and only that root,
  * teardown ends the process and frees the port.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import pytest

from tests.isolated_dev_server import FORBIDDEN_LIVE_PORTS
from tests.isolated_dev_server import IsolatedDevServer
from tests.isolated_dev_server import assert_isolated_dev_server_port
from tests.isolated_dev_server import isolated_dev_server  # noqa: F401  -- fixture
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401  -- fixture
from tests.isolated_dev_server import finish_server_output, wait_until_serving
from tests.isolated_dev_server import REPO_ROOT, go_test_server_binary
from tests import isolated_dev_server as server_fixture
from tools import verify_go_agent_status_live as verifier
from tools import go_binary
from tools.instance_isolation import synthetic_test_environment


pytestmark = pytest.mark.socket


@pytest.mark.parametrize("consumer", ["tests.test_agent_status_docker", "tools.verify_go_agent_status_live"])
def test_curated_runtime_consumers_do_not_import_browser_fixtures(consumer):
    script = """
import importlib
import importlib.abc
import sys

class ExcludeBrowserFixtures(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"tests.gate_harness", "tests.isolated_dev_server"}:
            raise ModuleNotFoundError("curated runtime excludes heavyweight fixture: " + fullname)

sys.meta_path.insert(0, ExcludeBrowserFixtures())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, consumer], cwd=REPO_ROOT,
        env=synthetic_test_environment(), capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr



def test_isolated_server_build_includes_native_chat_storage(isolated_dev_server):
    status, _headers, body = isolated_dev_server.request("/api/chat/bootstrap?browser_instance_id=fixture-capability")
    assert status == HTTPStatus.OK, body.decode() + "\n" + "\n".join(isolated_dev_server.output)
    assert "native chat storage unavailable" not in "\n".join(isolated_dev_server.output)


def test_live_collector_verifier_uses_complete_native_fixture_builder(tmp_path, monkeypatch):

    class BuilderReached(Exception):
        pass

    def complete_builder(source_root):
        assert source_root == REPO_ROOT
        raise BuilderReached()

    def fixture_command(*command, **_kwargs):
        if command[:2] == ("go", "build"):
            raise AssertionError("verifier bypassed the complete native fixture builder")
        return subprocess.CompletedProcess(command, 0, "private fixture pane", "")

    monkeypatch.delenv("YOMUX_GO_BINARY", raising=False)
    monkeypatch.delenv("YOLOMUX_GO_BINARY", raising=False)
    monkeypatch.setattr(verifier, "go_test_server_binary", complete_builder, raising=False)
    monkeypatch.setattr(verifier, "run", fixture_command)
    with pytest.raises(BuilderReached):
        verifier.main()


@pytest.mark.parametrize("changed_input", ["embedded", "toolchain", "recipe"])
def test_fixture_binary_cache_tracks_build_identity(tmp_path, monkeypatch, changed_input):
    monkeypatch.delenv("YOMUX_GO_BINARY", raising=False)
    monkeypatch.setattr(go_binary.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    (tmp_path / "version.yaml").write_text('version: "1.2.3"\n', encoding="utf-8")
    embedded = tmp_path / "cmd/yomux-go/pages/main.gohtml"
    embedded.parent.mkdir(parents=True)
    embedded.write_text("original template", encoding="utf-8")
    toolchain = ["original toolchain"]
    monkeypatch.setattr(go_binary.subprocess, "check_output", lambda *_args, **_kwargs: toolchain[0])
    builds = []

    def build(command, **_kwargs):
        builds.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(b"fixture binary")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(go_binary.subprocess, "run", build)
    first = go_test_server_binary(tmp_path)
    assert go_test_server_binary(tmp_path) == first
    assert len(builds) == 1
    if changed_input == "embedded":
        embedded.write_text("changed template", encoding="utf-8")
    elif changed_input == "toolchain":
        toolchain[0] = "changed toolchain"
    else:
        monkeypatch.setattr(go_binary, "GO_TEST_SERVER_BUILD_FLAGS", ("-buildvcs=false", "-tags=sqlite_fts5", "-trimpath"), raising=False)
    assert go_test_server_binary(tmp_path) != first
    assert len(builds) == 2
    assert all("-tags=sqlite_fts5" in command for command in builds)


def test_server_output_drains_after_readiness_without_blocking_child():
    program = (
        "import sys; print('YOmux Go backend version=test addr=127.0.0.1:1234 scheme=http', flush=True); "
        "sys.stdout.write('x'*1048576+'\\n'); sys.stdout.flush()"
    )
    process = subprocess.Popen([sys.executable, '-c', program], stdout=subprocess.PIPE, text=True)
    output = []
    reader = None
    try:
        reader = wait_until_serving(process, 1234, output)
        finish_server_output(process, output, reader)
        assert process.returncode == 0
        assert not reader.is_alive()
        assert len(output[-1]) == 1048576
    finally:
        if process.poll() is None:
            process.kill()
        finish_server_output(process, output, reader)

# Every writable location the instance is told about, and the `BuildPaths` attribute that owns it.
ISOLATED_ENVIRONMENT_ROOTS = (
    ("YOLOMUX_CONFIG_DIR", "config_dir"),
    ("YOLOMUX_STATE_DIR", "state_dir"),
    ("YOLOMUX_CACHE_DIR", "cache_dir"),
    ("YOLOMUX_RUNTIME_DIR", "runtime_dir"),
    ("YOLOMUX_LOG_DIR", "log_dir"),
    ("YOLOMUX_WORKSPACE_BASE", "workspace_dir"),
    ("HOME", "home_dir"),
    ("TMPDIR", "tmp_dir"),
)


def _running_environment(pid: int) -> dict[str, str]:
    """The environment the SERVER PROCESS is actually running under, read from the kernel."""

    raw = Path(f"/proc/{pid}/environ").read_bytes()
    entries = [entry.decode("utf-8", "replace") for entry in raw.split(b"\0") if entry]
    return dict(entry.split("=", 1) for entry in entries if "=" in entry)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads the running process environment from /proc")
def test_m13_an_isolated_dev_server_serves_and_owns_every_root_it_writes(
    isolated_dev_server: IsolatedDevServer,
) -> None:
    server = isolated_dev_server
    assert Path(server.process.args[0]).name == "yomux-go", server.process.args
    assert "--backend" not in server.process.args

    # 1. It is serving, and it is the product answering rather than a socket the kernel is holding.
    server.assert_serving()
    status, _headers, body = server.request("/api/ping")
    assert status == HTTPStatus.OK, (status, body, server.output[-20:])
    assert json.loads(body)["ok"] is True

    # 2. Ephemeral, and never one of the operator's live servers.
    assert server.port not in FORBIDDEN_LIVE_PORTS, (server.port, sorted(FORBIDDEN_LIVE_PORTS))
    assert_isolated_dev_server_port(server.port)
    assert server.base_url == f"http://127.0.0.1:{server.port}"

    running = _running_environment(server.process.pid)

    # 3. Every writable root the RUNNING process holds resolves inside this fixture's root. Read
    #    from the kernel, not from the dict passed to Popen, so an inherited variable that the
    #    product re-derived for itself would still be caught here.
    root = server.paths.root.resolve(strict=False)
    for variable, attribute in ISOLATED_ENVIRONMENT_ROOTS:
        value = Path(running[variable]).resolve(strict=False)
        assert value == getattr(server.paths, attribute).resolve(strict=False), (variable, value)
        assert value.is_relative_to(root), f"{variable}={value} escapes the fixture root {root}"

    # 4. Its own auth config, inside its own config directory. THIS is the clause that has cost
    #    two agents real time: an instance on a derived root reads the auth config in that root,
    #    so a cookie or a password from the operator's instance authenticates against nothing here.
    assert server.paths.auth_config_path.parent == server.paths.config_dir
    assert server.paths.auth_config_path.is_relative_to(root)
    assert Path(running["YOLOMUX_CONFIG_DIR"]).resolve(strict=False) != Path(
        os.environ.get("YOLOMUX_CONFIG_DIR", "/nonexistent-operator-config")
    ).resolve(strict=False)

    # 5. The private tmux socket, and a fixture-created session name.
    assert Path(running["YOLOMUX_TMUX_SOCKET"]) == Path(server.tmux.socket_path)
    assert server.tmux.sessions and all(name.startswith("yt-") for name in server.tmux.sessions)

    # 6. It really writes into that root. A root nothing writes to proves nothing about isolation.
    written = [path for path in root.rglob("*") if path.is_file()]
    assert written, root


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads the running process environment from /proc")
def test_m13_two_isolated_dev_servers_share_no_port_socket_or_root(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
) -> None:
    """Two at once, because the teardown-isolation work needs a peer to be undisturbed."""

    first = isolated_dev_server_factory("first")
    second = isolated_dev_server_factory("second")

    assert first.port != second.port
    assert Path(first.tmux.socket_path) != Path(second.tmux.socket_path)
    assert set(first.tmux.sessions).isdisjoint(second.tmux.sessions)
    assert not first.paths.root.resolve(strict=False).is_relative_to(second.paths.root.resolve(strict=False))
    assert not second.paths.root.resolve(strict=False).is_relative_to(first.paths.root.resolve(strict=False))

    first.assert_serving()
    second.assert_serving()


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads the running process environment from /proc")
def test_m13_teardown_ends_the_process_and_releases_the_port(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
) -> None:
    """Teardown is part of the harness contract, so it is asserted rather than left to the finalizer."""

    server = isolated_dev_server_factory("teardown")
    server.assert_serving()
    port = server.port

    server.stop()

    assert server.stopped is True
    assert server.process.poll() is not None, server.output[-20:]
    assert _port_is_free(port), f"port {port} is still bound after teardown"
    # Idempotent: the fixture finalizer stops every server again, and that must not raise.
    server.stop()


# Go is the subject; Python imports only construct the fixture or verify its ownership.
@pytest.mark.go_backend
def test_go_only_gate_rejects_python_selection_and_still_starts_go(
    monkeypatch: pytest.MonkeyPatch,
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("YOMUX_CHECK_GO_ONLY", "1")

    rejected_root = tmp_path / "rejected-runtime"
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("YOMUX_", "YOLOMUX_"))}
    environment["YOMUX_ROOT"] = str(rejected_root)
    result = subprocess.run([str(go_test_server_binary(REPO_ROOT)), "--launch", "--backend", "python"],
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2, result.stderr
    assert "backend" in result.stderr
    assert not rejected_root.exists()

    server = isolated_dev_server_factory("go-only-gate")
    assert Path(server.process.args[0]).name == "yomux-go", server.process.args
    assert "--backend" not in server.process.args

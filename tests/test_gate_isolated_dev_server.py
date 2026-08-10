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
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import pytest

from tests.isolated_dev_server import FORBIDDEN_LIVE_PORTS
from tests.isolated_dev_server import IsolatedDevServer
from tests.isolated_dev_server import assert_isolated_dev_server_port
from tests.isolated_dev_server import isolated_dev_server  # noqa: F401  -- fixture
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401  -- fixture


pytestmark = pytest.mark.socket

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

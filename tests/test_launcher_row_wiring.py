# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""W1: the supported launcher resolves ONE clean row plan per row and reuses that
exact captured plan for the server launch AND both authenticated probes, with no
inherited global background-owner primary-port export.

Two kinds of proof live here:

  * SOURCE CONTRACT over the live launcher and the shared macOS launcher text: the
    retired globals are gone, the row plan is captured once, and both probes run
    through `instance_isolation.py exec --plan-file` with that same captured plan.
  * ONE EPHEMERAL-PORT INTEGRATION test that actually starts a server through the
    exec mode, verifies it with the real `launcher_probe.py owner` subcommand bound
    to the unique listener PID, and tears down by exact process identity.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.gate_harness import HttpPortLease
from tests.isolated_dev_server import REPO_ROOT
from tests.isolated_dev_server import assert_isolated_dev_server_port
from tests.isolated_dev_server import build_environment
from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.isolated_dev_server import stop_and_reap_daemons
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from tools.instance_isolation import INSTANCE_ENV
from tools.instance_isolation import RowPlan


def _launcher_path() -> Path:
    override = os.environ.get("YOLO_DEV_START_SH")
    if override:
        return Path(override)
    return Path.home() / "dev" / "ai-config" / "claude" / "skills" / "yolo-dev-start" / "yolo-dev-start.sh"


LAUNCHER = _launcher_path()
STARTUP_COMMON = REPO_ROOT / "tools" / "startup_common.sh"

_LAUNCHER_MISSING = pytest.mark.skipif(
    not LAUNCHER.is_file(), reason=f"live launcher not present at {LAUNCHER}"
)


@_LAUNCHER_MISSING
def test_launcher_has_no_inherited_global_primary_port_export() -> None:
    """The global background-owner primary-port export and the Darwin launchctl
    setenv are both gone; each row now carries its own clean environment."""
    text = LAUNCHER.read_text(encoding="utf-8")

    assert "export YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT" not in text
    assert "launchctl setenv" not in text
    # The one retired-var family is gone from the launcher entirely.
    assert "YOLOMUX_EARLY_INSTANCE_PORT" not in text
    assert "YOLOMUX_MANAGED_INSTANCE_PORT" not in text


@_LAUNCHER_MISSING
def test_launcher_captures_one_row_plan_and_reuses_it_for_server_and_both_probes() -> None:
    """One plan is captured per row and the server launch plus BOTH probes run
    through `instance_isolation.py exec --plan-file` with that same captured plan."""
    text = LAUNCHER.read_text(encoding="utf-8")

    # The plan is resolved once per row and stored under the row's name.
    assert "instance_isolation.py plan --port" in text
    assert 'ROW_PLAN[$name]="$plan_file"' in text

    # The Linux server launch runs the server through the exec mode under the plan.
    assert 'launch_server "$dir" "$server_port" "$plan_file"' in text
    assert 'instance_isolation.py exec --plan-file "$plan_file" --' in text
    assert "yolomux.py --host" in text

    # Both probes take a plan-file argument and run the shared launcher_probe.py
    # subcommands through the exec mode.
    assert 'launcher_probe.py --scheme https owner' in text
    assert 'launcher_probe.py --scheme https sessions' in text
    # verify_owner and verify_sessions both wrap launcher_probe.py in the exec mode.
    assert text.count('instance_isolation.py exec --plan-file "$plan_file" --') >= 3

    # The captured plan reaches the server, the owner probe, and the sessions probe.
    assert 'verify_owner "$dir" "$server_port" "$plan_file" "$listener_pid"' in text
    assert 'verify_sessions "$dir" "$server_port" "${ROW_PLAN[$name]:-}"' in text

    # The owner probe is bound to the one unique listener PID.
    assert 'listener_pid="$(yolomux_unique_listener_pid "$server_port")"' in text


@_LAUNCHER_MISSING
def test_launcher_macos_carries_the_plan_through_the_shared_launcher() -> None:
    """The macOS row also launches through the exec plan (via the shared launcher),
    passing the plan file and a primary port only for the default/durable row."""
    text = LAUNCHER.read_text(encoding="utf-8")

    assert 'export YOLOMUX_ROW_PLAN_FILE="$plan_file"' in text
    assert 'if [ "$server_port" = "$PLATFORM_DEFAULT_PORT" ]; then macos_primary="$server_port"; else macos_primary=""; fi' in text


def test_shared_macos_launcher_execs_plan_when_present_and_stays_direct_otherwise() -> None:
    """The shared macOS launcher never reads roots or a plan path from tmux's
    retained environment; both launch paths pass one plan as an argument."""
    text = STARTUP_COMMON.read_text(encoding="utf-8")

    assert 'plan_json=$8; shift 8' in text
    assert 'instance_isolation.py" exec --plan-json "$plan_json" --' in text
    assert 'instance_isolation.py" plan-direct --port "$port"' in text
    assert 'YOLOMUX_ROW_PLAN_FILE:-' not in text.split("yolomux_macos_server_launcher()", 1)[1].split("yolomux_submit_macos_server()", 1)[0]


@pytest.mark.socket
@pytest.mark.skipif(not Path("/proc").is_dir(), reason="teardown checks the process via /proc")
def test_exec_launched_server_passes_the_real_owner_probe_and_tears_down_by_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End to end on an ephemeral port: start a managed self-owner through the exec
    mode, verify it with the real launcher_probe owner subcommand bound to the
    unique listener PID (reusing the exact same captured plan file), tear down by
    exact process identity, and confirm nothing survives."""
    root = tmp_path / "runtime"
    paths = build_paths(root)
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)

    # Reserve the ephemeral port so the plan can name it, then hand it over.
    lease = HttpPortLease.reserve()
    port = assert_isolated_dev_server_port(lease.port)
    lease.release()

    # A no-strip plan that keeps the harness's explicit isolation roots but marks
    # this row a managed self-owner, so the app runs DisabledBackgroundOwner.
    plan = RowPlan(unset=(), assign={INSTANCE_ENV: f"{port}:managed"})

    server = None
    try:
        server = start_isolated_dev_server(
            "row-exec",
            REPO_ROOT,
            paths,
            tmux_runtime,
            port=port,
            exec_plan_json=plan.to_json(),
        )
        server.assert_serving()

        # The exec chain preserves the PID, so the server process is the listener.
        listener_pid = server.process.pid

        env = build_environment(REPO_ROOT, paths, tmux_runtime, port, auth_bypass=True)
        # Reuse the exact plan file the server launched under -- the same captured
        # plan feeds the server and this probe.
        plan_path = paths.root / "row-plan.json"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "instance_isolation.py"),
                "exec",
                "--plan-file",
                str(plan_path),
                "--",
                sys.executable,
                str(REPO_ROOT / "tools" / "launcher_probe.py"),
                "--scheme",
                "http",
                "--host",
                "127.0.0.1",
                "--timeout",
                "30",
                "owner",
                "--port",
                str(port),
                "--listener-pid",
                str(listener_pid),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, (result.stdout, result.stderr, server.output[-20:])
        assert f"owner {port} (managed)" in result.stdout, result.stdout

        # Teardown by exact PID identity.
        reaped = stop_and_reap_daemons(server)
        assert server.process.poll() is not None, server.output[-20:]
        assert isinstance(reaped, list)
    finally:
        if server is not None and server.process.poll() is None:
            stop_and_reap_daemons(server)
        stop_isolated_tmux_runtime(tmux_runtime)

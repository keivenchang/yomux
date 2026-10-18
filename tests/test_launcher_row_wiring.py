# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native managed rows reuse one captured plan for launch and authenticated discovery."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from tools.tmux_paths import short_temp_base, short_temp_prefix

import pytest

from tests.gate_harness import HttpPortLease
from tests.isolated_dev_server import REPO_ROOT
from tests.isolated_dev_server import go_test_server_binary
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
    override = os.environ.get("YOMUX_DEV_START_SH") or os.environ.get("YOLO_DEV_START_SH")
    if override:
        return Path(override)
    candidates = (
        Path.home() / ".agents" / "skills" / "yomux-dev-start" / "yomux-dev-start.sh",
        Path.home() / "dev" / "ai-config" / "agents" / "skills" / "yomux-dev-start" / "yomux-dev-start.sh",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


LAUNCHER = _launcher_path()
STARTUP_COMMON = REPO_ROOT / "tools" / "startup_common.sh"

_LAUNCHER_MISSING = pytest.mark.skipif(
    not LAUNCHER.is_file(), reason=f"live launcher not present at {LAUNCHER}"
)


@_LAUNCHER_MISSING
def test_launcher_has_no_inherited_global_primary_port_export() -> None:
    """The global scheduler primary-port export and the Darwin launchctl
    setenv are both gone; each row now carries its own clean environment."""
    text = LAUNCHER.read_text(encoding="utf-8")

    assert "export YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT" not in text
    assert "launchctl setenv" not in text
    # The one retired-var family is gone from the launcher entirely.
    assert "YOLOMUX_EARLY_INSTANCE_PORT" not in text
    assert "YOLOMUX_MANAGED_INSTANCE_PORT" not in text


@_LAUNCHER_MISSING
def test_launcher_captures_one_row_plan_and_reuses_it_for_server_and_both_probes() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert '--operator plan --root "$dir" --port "$server_port" > "$plan_file"' in text
    assert 'ROW_PLAN[$current_row_index]="$plan_file"' in text
    assert '--operator exec --root "$dir" --plan-file "$plan_file" -- env' in text
    assert '--operator sessions --root "$dir" --port "$server_port" --plan-file "$plan_file"' in text
    assert '--operator verify-environment --root "$dir" --port "$server_port" --pid "$listener_pid" --plan-file "$plan_file"' in text


@_LAUNCHER_MISSING
def test_launcher_macos_carries_the_plan_through_the_shared_launcher() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert '"$(product_env_name ROW_PLAN_FILE)=$plan_file"' in text


def test_shared_macos_launcher_execs_plan_when_present_and_stays_direct_otherwise() -> None:
    text = STARTUP_COMMON.read_text(encoding="utf-8")
    assert 'plan_json=$7; env_count=$8; shift 8' in text
    assert 'export "$assignment"' in text
    assert 'product_env_unset "$env_suffix"' in text
    assert '--operator exec --root "$repo" --plan-json "$plan_json" --' in text
    assert '--launch --launch-plan-json "$plan_json" --root "$repo"' in text
    assert '--operator plan-direct --root "$repo_root" --port "$port"' in text


def test_isolated_server_environment_does_not_inherit_a_parent_root(monkeypatch, tmp_path) -> None:
    """Explicit fixture roots must not be replaced by the parent server's root."""
    monkeypatch.setenv("YOLOMUX_ROOT", "/tmp/foreign-parent-root")
    paths = build_paths(tmp_path / "instance")
    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path, session_count=1)
    try:
        environment = build_environment(REPO_ROOT, paths, runtime, 7901)
        assert "YOLOMUX_ROOT" not in environment
        overridden = build_environment(
            REPO_ROOT,
            paths,
            runtime,
            7901,
            env_overrides={"YOLOMUX_ROOT": str(tmp_path / "explicit-root")},
        )
        assert overridden["YOLOMUX_ROOT"] == str(tmp_path / "explicit-root")
    finally:
        stop_isolated_tmux_runtime(runtime)


@pytest.mark.socket
@pytest.mark.skipif(not Path("/proc").is_dir(), reason="teardown checks the process via /proc")
def test_exec_launched_server_passes_the_real_session_probe_and_tears_down_by_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End to end on an ephemeral port: start a managed instance through the exec
    mode, verify its tmux session with the real launcher probe (reusing the exact
    same captured plan file), tear down by exact process identity, and confirm
    nothing survives."""
    root = Path(tempfile.mkdtemp(prefix=short_temp_prefix("y-"), dir=short_temp_base()))
    paths = build_paths(root)
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)

    # Reserve the ephemeral port so the plan can name it, then hand it over.
    lease = HttpPortLease.reserve()
    port = assert_isolated_dev_server_port(lease.port)
    lease.release()

    # A no-strip plan that keeps the harness's explicit isolation roots but marks
    # this row as a managed native instance.
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

        # The isolated session is the launcher-visible readiness contract.
        session_name = tmux_runtime.sessions[0]

        env = build_environment(REPO_ROOT, paths, tmux_runtime, port, auth_bypass=True)
        # Reuse the exact plan file the server launched under -- the same captured
        # plan feeds the server and this probe.
        plan_path = paths.root / "row-plan.json"
        result = subprocess.run(
            [
                str(go_test_server_binary(REPO_ROOT)),
                "--operator", "sessions", "--root", str(REPO_ROOT),
                "--plan-file", str(plan_path), "--http", "--host", "127.0.0.1",
                "--timeout", "30s", "--port", str(port), "--sessions", session_name,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, (result.stdout, result.stderr, server.output[-20:])

        # Teardown by exact PID identity.
        reaped = stop_and_reap_daemons(server)
        assert server.process.poll() is not None, server.output[-20:]
        assert isinstance(reaped, list)
    finally:
        if server is not None and server.process.poll() is None:
            stop_and_reap_daemons(server)
        stop_isolated_tmux_runtime(tmux_runtime)
        shutil.rmtree(root)

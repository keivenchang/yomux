# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native managed rows reuse one captured plan for launch and authenticated discovery."""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from tools.tmux_paths import short_temp_base, short_temp_prefix

import pytest

from tests.gate_harness import HttpPortLease
from tests.gate_harness import run_fixture_cleanup_phases
from tests.browser_helpers.browser_layout import start_isolated_browser_app
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


def _launcher_path(environment=None):
    values = os.environ if environment is None else environment
    override = values.get("YOMUX_DEV_START_SH") or values.get("YOLO_DEV_START_SH")
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(f"explicit launcher audit target is missing: {path}")
        return path
    return None


LAUNCHER = _launcher_path()
STARTUP_COMMON = REPO_ROOT / "tools" / "startup_common.sh"

@pytest.mark.parametrize("dangerously_yolo", [False, True])
def test_browser_runtime_keeps_per_launch_choices_after_restart(monkeypatch, tmp_path, dangerously_yolo):
    runtime = start_isolated_browser_app(monkeypatch, tmp_path, dangerously_yolo=dangerously_yolo)
    try:
        for restart in (False, True):
            if restart:
                runtime.app.restart()
            status, _headers, body = runtime.app.request("/")
            assert status == 200
            match = re.search(rb'<script id="yomux-bootstrap" type="application/json">(.*?)</script>', body, re.DOTALL)
            assert match is not None
            bootstrap = json.loads(match[1])
            assert "dangerouslyYolo" not in bootstrap
            for agent in ("claude", "codex"):
                assert bootstrap["agentLaunchCommands"][agent]["normal"] == agent
                assert "--dangerously-" in bootstrap["agentLaunchCommands"][agent]["full_access"]
    finally:
        runtime.close()


_LAUNCHER_MISSING = pytest.mark.skipif(
    LAUNCHER is None, reason="external launcher audit requires YOMUX_DEV_START_SH"
)


def test_external_launcher_audit_only_uses_an_explicit_target(tmp_path, monkeypatch):
    installed = tmp_path / ".agents/skills/yomux-dev-start/yomux-dev-start.sh"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"unrelated installed revision")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _launcher_path({}) is None
    assert _launcher_path({"YOMUX_DEV_START_SH": str(installed)}) == installed
    with pytest.raises(FileNotFoundError, match="explicit launcher audit target"):
        _launcher_path({"YOMUX_DEV_START_SH": str(tmp_path / "missing")})


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
    assert '"$native_binary" --operator exec --root "$dir" --plan-file "$plan_file" --' in text
    assert '/bin/bash "$dir/boot.sh" --force --no-dev' in text
    assert '"$(product_env_name INHERITED_START_LOCK_PATH)=$(yomux_start_lock_path)"' in text
    assert '"$(product_env_name INHERITED_START_LOCK_PID)=$BASHPID"' in text


def test_shared_macos_launcher_execs_plan_when_present_and_stays_direct_otherwise() -> None:
    text = STARTUP_COMMON.read_text(encoding="utf-8")
    assert 'plan_json=$7; env_count=$8; shift 8' in text
    assert 'export "$assignment"' in text
    assert 'product_env_unset "$env_suffix"' in text
    assert '--operator exec --root "$repo" --plan-json "$plan_json" --' in text
    assert '--launch --launch-plan-json "$plan_json" --root "$repo"' in text
    assert '--operator plan-direct --root "$repo_root" --port "$port"' in text


def test_macos_server_uses_nohup_and_preserves_plan_without_tmux(tmp_path) -> None:
    binary = tmp_path / "native"
    binary.write_text(
        f'#!{sys.executable}\n'
        'import json\nimport os\nimport signal\nimport sys\n'
        'print(json.dumps({"hup": signal.getsignal(signal.SIGHUP), "tmux": os.environ.get("TMUX"), '
        '"pane": os.environ.get("TMUX_PANE"), "args": sys.argv[1:]}), flush=True)\n'
        'os.write(int(os.environ["TEST_READY_FD"]), b"ready")\n'
    )
    binary.chmod(0o755)
    plan = tmp_path / "plan.json"
    plan.write_text('{"unset":["YOMUX_ROOT"],"assign":{}}')
    log = tmp_path / "server.log"
    environment = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
                   "TMUX": "foreign-socket", "TMUX_PANE": "%999",
                   "YOMUX_ROW_PLAN_FILE": str(plan)}
    ready_read, ready_write = os.pipe()
    environment["TEST_READY_FD"] = str(ready_write)
    try:
        result = subprocess.run(
            ["/bin/bash", "-c", 'source "$1"; yomux_submit_macos_server "$2" "$3" /bin/bash "$PATH" 8880 "$4" 0 --port 8880',
             "bash", str(STARTUP_COMMON), str(REPO_ROOT), str(binary), str(log)],
            env=environment, capture_output=True, text=True, timeout=10, pass_fds=(ready_write,),
        )
        assert result.returncode == 0, result.stderr
        assert select.select([ready_read], [], [], 10)[0], log.read_text()
        assert os.read(ready_read, 5) == b"ready"
    finally:
        os.close(ready_read)
        os.close(ready_write)
    output = json.loads(log.read_text())
    assert output["hup"] == 1
    assert output["tmux"] is None
    assert output["pane"] is None
    assert output["args"][:3] == ["--operator", "exec", "--root"]
    assert output["args"][3] == str(REPO_ROOT)
    assert json.loads(output["args"][5]) == json.loads(plan.read_text())


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
        run_fixture_cleanup_phases("managed row", [
            ("server", lambda: stop_and_reap_daemons(server) if server is not None and server.process.poll() is None else None),
            ("tmux runtime", lambda: stop_isolated_tmux_runtime(tmux_runtime)),
            ("root", lambda: shutil.rmtree(root)),
        ])

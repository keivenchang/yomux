"""Gate N: completeness-sweep findings at the user-observable boundary."""

import ast
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import browser
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import HttpPortLease
from tests.gate_harness import run_fixture_cleanup_phases
from tests.isolated_dev_server import go_test_server_binary
from tests.test_native_launch import native_launch_environment  # noqa: F401
from tests.test_native_launch import run_native
from tests.helpers.browser_scenarios import assert_terminal_wheel_observation
from tests.helpers.browser_scenarios import terminal_wheel_observation
from yomux_lib.settings import default_settings
from yomux_lib.settings import read_settings_file
from yomux_lib.settings import write_settings_file


@pytest.mark.no_browser
def test_upgrade_cleanup_retires_previous_listener_after_replacement_failure():
    source = ast.parse(Path(__file__).read_text())
    target = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "test_n4_live_source_revision_change_restarts_without_orphaning_old_backends")
    boundary = next(node for node in ast.walk(target) if isinstance(node, ast.Try) and node.finalbody)
    stopped = []
    failure = RuntimeError("replacement retirement sentinel")

    class Process:
        def __init__(self, label):
            self.label = label

        def poll(self):
            return None

        def terminate(self):
            stopped.append(self.label)
            if self.label == "replacement":
                raise failure

        def wait(self, timeout):
            assert timeout == 20

    scope = {"previous": Process("previous"), "replacement": Process("replacement"),
             "run_fixture_cleanup_phases": run_fixture_cleanup_phases}
    with pytest.raises(RuntimeError) as raised:
        exec(compile(ast.Module(body=boundary.finalbody, type_ignores=[]), __file__, "exec"), scope)
    assert raised.value is failure
    assert stopped == ["replacement", "previous"]


@pytest.mark.browser
def test_n1_alternate_screen_wheel_scroll_reaches_the_terminal(browser, tmp_path):
    """An alternate-screen Claude pane routes wheel lines into xterm rather than losing upward scroll."""
    assert_terminal_wheel_observation(terminal_wheel_observation(browser, tmp_path))


@pytest.mark.browser
def test_n3_existing_settings_survive_new_defaults_after_upgrade(tmp_path):
    """An older, partial settings file retains its chosen value and gains every current default section."""
    path = tmp_path / "settings.yaml"
    write_settings_file({"appearance": {"theme": "light"}}, path)
    settings, error = read_settings_file(path)
    assert error == "", error
    assert settings["appearance"]["theme"] == "light"
    for section, defaults in default_settings().items():
        assert section in settings
        assert set(defaults) <= set(settings[section]), section


@pytest.mark.browser
def test_n4_live_source_revision_change_restarts_without_orphaning_old_backends(native_launch_environment, monkeypatch, tmp_path):
    """A pinned local source upgrade replaces the real listener with the matching native binary."""
    binary, environment = native_launch_environment
    repo = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "upgrade-source"
    checkout.mkdir()
    for directory in ("cmd", "internal", "static", "assets/runtime"):
        shutil.copytree(repo / directory, checkout / directory)
    for name in ("go.mod", "go.sum", "version.yaml", "tools/generate_go_version.py"):
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / name, destination)

    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(checkout), *arguments], text=True).strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("add", ".")
    git("commit", "-m", "running source revision")
    previous_sha = git("rev-parse", "HEAD")
    previous_version = run_native(binary, environment, "--version").stdout.strip().split()[-1]
    major, minor, patch = map(int, previous_version.split("."))
    target_version = f"{major}.{minor}.{patch + 1}"
    (checkout / "version.yaml").write_text(f'version: "{target_version}"\n')
    git("add", "version.yaml")
    git("commit", "-m", "target source revision")
    target_sha = git("rev-parse", "HEAD")
    # Reuse the hermetic native fixture builder, including its version overlay,
    # offline module cache, source identity and build lock.
    with monkeypatch.context() as build_environment:
        build_environment.delenv("YOMUX_GO_BINARY", raising=False)
        target_binary = go_test_server_binary(checkout)
    git("checkout", previous_sha)
    lease = HttpPortLease.reserve()
    port = lease.release()
    arguments = ("--launch", "--root", str(checkout), "--http", "--host", "127.0.0.1", "--port", str(port))
    ready_arguments = ("--operator", "ready", "--root", str(checkout), "--http", "--host", "127.0.0.1", "--port", str(port), "--timeout", "20s")
    previous = replacement = None
    with (tmp_path / "upgrade.log").open("w+") as output:
        try:
            previous = subprocess.Popen([str(binary), *arguments], env=environment, cwd=checkout, stdout=output, stderr=output)
            ready = run_native(binary, environment, *ready_arguments)
            assert ready.returncode == 0, ready.stderr + (tmp_path / "upgrade.log").read_text()
            owner_path = Path(environment["YOMUX_ROOT"]) / "instance.lock"
            assert json.loads(owner_path.read_bytes())["pid"] == previous.pid
            target_environment = dict(environment, YOMUX_GO_BINARY=str(target_binary))
            refused = run_native(target_binary, target_environment, *arguments, "--force")
            assert refused.returncode != 0, "a target binary must not replace a different source revision"
            assert previous.poll() is None
            assert run_native(binary, environment, *ready_arguments).returncode == 0
            git("merge", "--ff-only", target_sha)
            assert git("rev-parse", "HEAD") == target_sha
            replacement = subprocess.Popen([str(target_binary), *arguments, "--force"], env=target_environment, cwd=checkout, stdout=output, stderr=output)
            previous.wait(timeout=20)
            ready = run_native(target_binary, target_environment, *ready_arguments)
            assert ready.returncode == 0, ready.stderr + (tmp_path / "upgrade.log").read_text()
            assert replacement.poll() is None
            assert json.loads(owner_path.read_bytes())["pid"] == replacement.pid
            assert run_native(target_binary, target_environment, "--version").stdout.strip() == f"yomux-go {target_version}"
        finally:
            def retire(owned):
                if owned is not None and owned.poll() is None:
                    owned.terminate()
                    owned.wait(timeout=20)

            run_fixture_cleanup_phases("upgrade listeners", [
                ("replacement", lambda: retire(replacement)),
                ("previous", lambda: retire(previous)),
            ])


@pytest.mark.browser
def test_n8_daemons_load_chart_paints_in_system_view_when_service_load_data_exists(browser, tmp_path):
    """Given service-load data, System -> Daemons paints SVG marks and Graphs has no Daemons Load card."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    activated = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        selectSession('__debug__').then(() => requestAnimationFrame(() => done(true)))
          .catch(error => done({error: String(error)}));
        """
    )
    assert activated is True, activated
    result = browser.execute_script(
        """
        const now = Date.now();
        setDebugSubTab('system');
        clearJsDebugGraphData();
        debugGraphApplyServerHistory({latest_sequence: 1, records: [{
          start: Math.floor(now / 1000), duration: 1,
          host_metrics: {service_load: {'runtime:agent_status': {
            label: 'agent status collector', cpu_samples: 1, cpu_total_percent: 42,
          }}},
        }]});
        jsDebugSystemState.payload = {
          ok: true, generated_at: now / 1000, server: {}, owner: {}, refresh: {}, search_index: {}, caches: {},
          client_events: {}, chat: {}, cpu_budget: {},
          local_services: {schema_version: 5, inventory: [], totals: {}, services: []},
          top_endpoints: [], top_background_work: [],
        };
        refreshDebugSystemViews();
        const graph = document.querySelector('[data-js-debug-subview="graph"]');
        const system = document.querySelector('[data-js-debug-subview="system"]');
        const chart = system?.querySelector('[data-js-debug-chart="serversLoad"]');
        const marks = chart?.querySelectorAll('[data-js-debug-area-series], [data-js-debug-series]').length || 0;
        return {graphChart: Boolean(graph?.querySelector('[data-js-debug-chart="serversLoad"]')), systemChart: Boolean(chart), marks};
        """
    )
    assert result == {"graphChart": False, "systemChart": True, "marks": 1}, result

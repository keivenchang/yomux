"""Gate N: completeness-sweep findings at the user-observable boundary."""

import shlex
import sys
from urllib.parse import urlencode

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import start_isolated_browser_app
from tests.browser_helpers.browser_layout import stop_browser_fixture
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.helpers.browser_scenarios import assert_terminal_wheel_observation
from tests.helpers.browser_scenarios import terminal_wheel_observation
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from yomux_lib.settings import default_settings
from yomux_lib.settings import read_settings_file
from yomux_lib.settings import write_settings_file


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
def test_n4_live_source_revision_change_restarts_without_orphaning_old_backends():
    """NOT-APPLICABLE: v0.6.10 has no fixture-owned source-revision deployment transition API; F9 must provide it."""
    pytest.skip("NOT-APPLICABLE on v0.6.10; no fixture-owned source-revision transition API before F9")


@pytest.mark.browser
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_n5_real_terminal_wrap_keeps_full_paths_and_passive_misses_are_not_errors(
    browser,
    monkeypatch,
    gate_runtime_paths,
):
    """A real tmux/xterm soft wrap preserves full path tokens without erroring on speculative misses."""

    runtime = start_isolated_browser_app(monkeypatch, gate_runtime_paths.root, dangerously_yolo=False)
    session = runtime.sessions[0]
    existing_path = gate_runtime_paths.root / "workspace" / "terminal-wrap-existing.py"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_text("print('fixture')\n", encoding="utf-8")
    missing_path = "/tmp/instruction-fleet-acceptance-bar-measurement.md"
    server, thread = start_browser_server(
        monkeypatch,
        runtime.paths.config_dir,
        runtime.app,
        auth_bypass=True,
        paths=gate_runtime_paths,
    )
    try:
        browser.get(
            f"http://127.0.0.1:{server.server_address[1]}/?"
            + urlencode({"sessions": session, "layout": "left", "tabs": f"left:{session}"})
        )
        assert_live_runtime_boot_healthy(browser, "terminal-wrap-path-gate", timeout=12)
        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                "return terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN;",
                session,
            )
        )
        cols = WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return terminals.get(arguments[0])?.term?.cols || 0;",
                session,
            )
        )
        marker = "terminal-wrap-evidence:"
        prefix = f"{marker}{'x' * max(1, int(cols) - len(marker) - 8)} "
        output_line = f"{prefix}{existing_path} {missing_path}"
        code = f"print({output_line!r}, flush=True)"
        command = shlex.join((sys.executable, "-c", code))
        sent = run_isolated_tmux(
            runtime.tmux,
            "send-keys",
            "-t",
            f"{session}:",
            command,
            "Enter",
            timeout=5,
        )
        assert sent.returncode == 0, sent.stderr or sent.stdout
        pane_ready, panes = wait_for_isolated_tmux_panes(
            runtime.tmux,
            (session,),
            lambda observed: marker in observed.get(session, ""),
            timeout=5,
            join_wrapped_lines=True,
        )
        assert pane_ready, panes

        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const term = terminals.get(arguments[0])?.term;
                if (!term?.buffer?.active?.getLine) return false;
                for (let index = 0; index < term.buffer.active.length; index += 1) {
                  if (term.buffer.active.getLine(index)?.translateToString?.(true)?.includes(arguments[1])) return true;
                }
                return false;
                """,
                session,
                marker,
            )
        )
        measured = browser.execute_async_script(
            """
            const session = arguments[0];
            const marker = arguments[1];
            const done = arguments[arguments.length - 1];
            const term = terminals.get(session)?.term;
            (async () => {
              const markerRows = [];
              for (let index = 0; index < term.buffer.active.length; index += 1) {
                if (term.buffer.active.getLine(index)?.translateToString?.(true)?.includes(marker)) markerRows.push(index + 1);
              }
              const rows = [...new Set(markerRows.flatMap(row => Array.from({length: 8}, (_, offset) => row + offset)))]
                .filter(row => row <= term.buffer.active.length);
              const references = rows.flatMap(row => terminalWrappedLineReferences(term, row).filter(ref => ref.type === 'file'));
              await Promise.all(rows.map(row => terminalReferenceProviderLinks(session, term, row)));
              done({
                cols: term.cols,
                rows,
                references: references.map(ref => ({path: ref.path, range: ref.range})),
                clientFailures: jsDebugEvents.filter(event => event?.type === 'client_failure'),
              });
            })().catch(error => done({error: String(error?.stack || error)}));
            """,
            session,
            marker,
        )
        assert "error" not in measured, measured
        paths = {reference["path"] for reference in measured["references"]}
        assert str(existing_path) in paths, measured
        assert missing_path in paths, measured
        assert "/terminal-wrap-existing.py" not in paths, measured
        assert "/tmp/instruction-" not in paths, measured
        assert measured["clientFailures"] == [], measured
    finally:
        stop_browser_fixture(server, thread, runtime, browser=browser)


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

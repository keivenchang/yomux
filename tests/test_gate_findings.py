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

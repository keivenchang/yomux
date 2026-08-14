"""Gate N: completeness-sweep findings at the user-observable boundary."""

import shlex
import sys
from urllib.parse import urlencode

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import start_isolated_browser_app
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.browser_helpers.browser_layout import stop_isolated_browser_app
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.helpers.browser_scenarios import assert_terminal_wheel_observation
from tests.helpers.browser_scenarios import terminal_wheel_observation
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from yolomux_lib.settings import default_settings
from yolomux_lib.settings import read_settings_file
from yolomux_lib.settings import write_settings_file


@pytest.mark.browser
def test_n1_alternate_screen_wheel_scroll_reaches_the_terminal(browser, tmp_path):
    """An alternate-screen Claude pane routes wheel lines into xterm rather than losing upward scroll."""
    assert_terminal_wheel_observation(terminal_wheel_observation(browser, tmp_path))


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 Daemons subsystem surface")
def test_n2_daemons_process_cards_render_real_telemetry(browser, tmp_path):
    """Server, daemon, and storaged cards each render PID, uptime, CPU and RSS rather than placeholders."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    cards = browser.execute_script(
        """
        return ['server', 'daemon', 'storaged'].map(name => ({name, card: document.querySelector(`[data-daemon-card="${name}"]`)?.textContent || ''}));
        """,
    )
    assert all(card["card"].strip() and "—" not in card["card"] for card in cards), cards


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
        stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 SubsystemSpec Preferences controls")
def test_n7_preferences_service_controls_group_features_contiguously(browser, tmp_path):
    """Feature-labelled Preferences controls occur in one contiguous run for each feature."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    groups = browser.execute_script("return Array.from(document.querySelectorAll('[data-subsystem-feature-group]')).map(node => node.dataset.subsystemFeatureGroup);")
    assert groups and groups == list(dict.fromkeys(groups)), groups


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 Daemons Load graph")
def test_n8_daemons_load_graph_paints_when_service_load_data_exists(browser, tmp_path):
    """Given service-load data, the Daemons chart has painted SVG/canvas marks rather than an empty panel."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    marks = browser.execute_script("return document.querySelectorAll('[data-daemons-load-graph] svg path, [data-daemons-load-graph] canvas').length;")
    assert marks > 0, marks


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 Daemons card layout")
def test_n9_daemons_cards_have_no_dead_space_and_equal_heights(browser, tmp_path):
    """Cards sharing a Daemons row have comparable heights and their content fills each card."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    geometry = browser.execute_script(
        """
        return Array.from(document.querySelectorAll('[data-daemon-card]')).map(node => {
          const rect = node.getBoundingClientRect();
          const children = Array.from(node.children).map(child => child.getBoundingClientRect());
          const contentTop = Math.min(...children.map(child => child.top));
          const contentBottom = Math.max(...children.map(child => child.bottom));
          return {name: node.dataset.daemonCard, top: rect.top, height: rect.height, contentHeight: contentBottom - contentTop};
        });
        """,
    )
    assert len(geometry) == 3, geometry
    rows = {}
    for card in geometry:
        rows.setdefault(round(card["top"]), []).append(card)
        assert card["contentHeight"] / card["height"] >= 0.75, card
    for cards in rows.values():
        heights = [card["height"] for card in cards]
        assert max(heights) - min(heights) <= 2, cards


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 Daemons tab")
def test_n10_daemons_tab_contains_only_daemon_process_content(browser, tmp_path):
    """The Daemons tab owns exactly the three process cards, not unrelated subsystem content."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    membership = browser.execute_script(
        """
        const tab = document.querySelector('[data-daemons-tab]');
        if (!tab) return null;
        return {
          cards: Array.from(tab.querySelectorAll(':scope [data-daemon-card]')).map(card => ({
            name: card.dataset.daemonCard,
            processRows: card.querySelectorAll('[data-daemon-process]').length,
          })),
          unrelated: Array.from(tab.querySelectorAll('[data-subsystem-owner], [data-chat-message-id], [data-cache-card]')).map(node => node.outerHTML),
        };
        """,
    )
    assert membership is not None, membership
    assert [card["name"] for card in membership["cards"]] == ["server", "daemon", "storaged"], membership
    assert all(card["processRows"] == 1 for card in membership["cards"]), membership
    assert not membership["unrelated"], membership

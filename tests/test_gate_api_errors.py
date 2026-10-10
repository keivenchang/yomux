# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Authenticated real-session gate for non-2xx API responses.

The journey deliberately excludes the YO!stats panel. Opening ``__yocost__`` against a
freshly started fixture server produces real product Warnings ("YO!stats stream generation
stalled for more than 3s", route ``/api/stats-stream``) and clamps every selected range back
to 300s, so the panel cannot yet be driven through the strict browser diagnostic gate. That
stall remains an unresolved post-release YO!stats coverage item; the stats pending-response contract this gate accompanies is already asserted at the
HTTP layer by ``tests/test_stats_current_http.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any
import uuid

import pytest
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_console import assert_only_expected_browser_http_error
from tests.browser_helpers.browser_console import read_browser_console_log
from tests.gate_harness import assert_api_journey_error_free
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.helpers.browser_contracts import send_native_key
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]

FORCED_MISSING_ROUTE = "/api/api-sweep-forced-missing"


@pytest.fixture(autouse=True)
def api_sweep_workspace(gate_runtime_paths, gate_tmux) -> dict[str, Path]:
    """Create one real changed Git repo and make it the private session cwd before app startup."""

    repo = gate_runtime_paths.home_dir / "dev" / "api-sweep-repo"
    source_dir = repo / "src"
    source_dir.mkdir(parents=True)
    target = source_dir / "changed.txt"
    target.write_text("committed API sweep content\n", encoding="utf-8")
    for command in (
        ("git", "init", "-q", "-b", "main", str(repo)),
        ("git", "-C", str(repo), "config", "user.name", "API Sweep Fixture"),
        ("git", "-C", str(repo), "config", "user.email", "api-sweep@example.invalid"),
        ("git", "-C", str(repo), "add", "src/changed.txt"),
        ("git", "-C", str(repo), "commit", "-q", "-m", "fixture baseline"),
    ):
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    target.write_text("working API sweep content\n", encoding="utf-8")

    session = str(gate_tmux.sessions[0])
    marker = f"api-sweep-cwd-{os.getpid()}"
    shell_command = f"cd {shlex.quote(str(repo))} && printf '{marker}\\n'"
    sent = run_isolated_tmux(gate_tmux, "send-keys", "-t", f"{session}:", shell_command, "Enter", timeout=5)
    assert sent.returncode == 0, sent.stderr or sent.stdout
    observed, panes = wait_for_isolated_tmux_panes(
        gate_tmux,
        (session,),
        lambda captured: marker in captured.get(session, ""),
        timeout=8,
        join_wrapped_lines=True,
    )
    assert observed, panes
    return {"repo": repo, "source_dir": source_dir, "target": target}


@pytest.fixture
def api_sweep_browser(api_sweep_workspace, authenticated_e2e_browser):
    """Order workspace creation before the shared authenticated app/browser fixture."""

    del api_sweep_workspace
    return authenticated_e2e_browser


def _load_api_sweep_page(harness: Any) -> str:
    session = str(harness.runtime.tmux.sessions[0])
    return harness.load(tabs=("files", "diff", session))


class _DrainedBrowserLog:
    """Replay Chrome log entries already drained while waiting for a deliberate failure.

    ``read_browser_console_log`` drains Chrome's log, so a bounded wait for an asynchronously
    reported network failure cannot re-read it. Accumulate the drained entries and hand them to
    the shared matcher rather than growing a second copy of its matching rules.
    """

    def __init__(self, entries: list[dict[str, Any]]):
        self._entries = entries

    def get_log(self, name: str) -> list[dict[str, Any]]:
        assert name == "browser", name
        return list(self._entries)


def _consume_deliberate_browser_http_error(harness: Any, *, path: str, status: int) -> dict[str, Any]:
    """Wait for the injected failure to reach Chrome's log, then consume only that entry.

    The candidate retires every real browser through a strict diagnostic gate, so a deliberately
    injected non-2xx must be claimed by the test that caused it or teardown reports it as unowned.
    """

    drained: list[dict[str, Any]] = []

    def reported(driver) -> bool:
        drained.extend(read_browser_console_log(driver))
        return any(path in str(entry.get("message") or "") for entry in drained)

    WebDriverWait(harness.driver, 12).until(reported)
    return assert_only_expected_browser_http_error(
        _DrainedBrowserLog(drained),
        path=path,
        status=status,
        query={},
    )


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_api_journey_gate_fires_on_injected_nonexistent_route(api_sweep_browser: Any) -> None:
    """Forced red: one real 404 carries method, route, status, and body into the gate failure."""

    _load_api_sweep_page(api_sweep_browser)
    api_sweep_browser.reset_api_journey_observations()
    response = api_sweep_browser.driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetch('/api/api-sweep-forced-missing').then(async response => done({
          status: response.status,
          body: await response.text(),
        })).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert response.get("status") == 404, response
    observations = api_sweep_browser.api_journey_observations()
    with pytest.raises(AssertionError, match="GET /api/api-sweep-forced-missing") as failure:
        assert_api_journey_error_free(observations)
    failure_text = str(failure.value)
    assert '"status": 404' in failure_text and '"body":' in failure_text, failure_text

    # The same real observation proves the allowlist: a written reason moves the response into
    # allowedNon2xx instead of failing, and an empty reason is rejected outright.
    allowed = assert_api_journey_error_free(
        observations,
        allowlist={("GET", FORCED_MISSING_ROUTE, 404): "Deliberate forced-red injection owned by this test."},
    )
    assert allowed["unexpectedNon2xx"] == [], allowed
    assert [entry["path"] for entry in allowed["allowedNon2xx"]] == [FORCED_MISSING_ROUTE], allowed
    with pytest.raises(ValueError, match="require written reasons"):
        assert_api_journey_error_free(observations, allowlist={("GET", FORCED_MISSING_ROUTE, 404): "   "})

    consumed = _consume_deliberate_browser_http_error(
        api_sweep_browser,
        path=FORCED_MISSING_ROUTE,
        status=404,
    )
    assert consumed["source"] == "network" and consumed["level"] == "SEVERE", consumed

    api_sweep_browser.reset_api_journey_observations()
    ping = api_sweep_browser.driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetch('/api/ping').then(response => done({status: response.status})).catch(error => done({error: String(error)}));
        """
    )
    assert ping == {"status": 200}, ping
    clean = assert_api_journey_error_free(api_sweep_browser.api_journey_observations())
    # The live app polls continuously (/api/auto-approve, /api/client-events, ...), so the observed
    # route set is not this test's to own. Gate the claim that is: the window carried /api/ping and
    # every response in it, product traffic included, was 2xx.
    assert clean["unexpectedNon2xx"] == [] and clean["allowedNon2xx"] == [], clean
    assert "GET /api/ping" in clean["observedRoutes"], clean


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_authenticated_normal_session_journey_has_no_unowned_non_2xx(
    api_sweep_browser: Any,
    api_sweep_workspace: dict[str, Path],
) -> None:
    """Derive requests from real user actions and reject every unowned non-2xx response."""

    session = str(api_sweep_browser.runtime.tmux.sessions[0])
    repo = api_sweep_workspace["repo"]
    source_dir = api_sweep_workspace["source_dir"]
    target = api_sweep_workspace["target"]
    _load_api_sweep_page(api_sweep_browser)

    dev = repo.parent
    api_sweep_browser.expand(dev, child_path=repo)
    api_sweep_browser.expand(repo, child_path=source_dir)
    api_sweep_browser.expand(source_dir, child_path=target)
    api_sweep_browser.re_expand(repo, child_path=source_dir)
    api_sweep_browser.expand(source_dir, child_path=target)

    target_row = api_sweep_browser.finder_row(target).element()
    ActionChains(api_sweep_browser.driver).double_click(target_row).perform()
    file_open = api_sweep_browser.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__yomuxTestWaitFor(() => {
          const panel = fileEditorPanelsForPath(path)[0];
          const text = panel?._cmView?.state?.doc?.toString?.() || '';
          return panel?.isConnected && Boolean(panel.querySelector('.cm-content')) && text.includes('working API sweep content') ? {
            loaded: true,
            text,
          } : null;
        }, {timeoutMs: 12000, description: `opened file ${path}`}).then(done, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
    )
    assert file_open.get("loaded") is True and "working API sweep content" in file_open.get("text", ""), file_open

    differ = api_sweep_browser.open_differ()
    first_differ = api_sweep_browser.assert_reaches_terminal_state(differ, bound=12)
    assert first_differ["terminal"] is True, first_differ
    api_sweep_browser.switch_session(session)
    reopened_differ = api_sweep_browser.open_differ()
    second_differ = api_sweep_browser.assert_reaches_terminal_state(reopened_differ, bound=12)
    assert second_differ["terminal"] is True, second_differ

    # The YO!stats leg of this journey is deliberately excluded; see the module docstring.
    api_sweep_browser.switch_session(session)
    WebDriverWait(api_sweep_browser.driver, 12).until(
        lambda driver: driver.execute_script(
            "return terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN "
            "&& Boolean(document.querySelector(`#term-${CSS.escape(arguments[0])} .xterm-screen`));",
            session,
        )
    )
    terminal_screen = api_sweep_browser.driver.find_element("css selector", f"#term-{session} .xterm-screen")
    terminal_screen.click()
    WebDriverWait(api_sweep_browser.driver, 8).until(
        lambda driver: driver.execute_script(
            "const input = document.querySelector(`#term-${arguments[0]} textarea`); "
            "return document.activeElement === input;",
            session,
        )
    )
    marker = f"apiinput{os.getpid()}"
    for character in marker:
        send_native_key(api_sweep_browser.driver, character)
    observed, panes = wait_for_isolated_tmux_panes(
        api_sweep_browser.runtime.tmux,
        (session,),
        lambda captured: marker in captured.get(session, ""),
        timeout=8,
        join_wrapped_lines=True,
    )
    assert observed, panes

    renamed = f"yt-{os.getpid()}-{uuid.uuid4().hex[:10]}-renamed"
    assert api_sweep_browser.driver.execute_script("return showSessionRenameDialog(arguments[0]);", session) is True
    rename_input = api_sweep_browser.driver.find_element("css selector", ".session-rename-dialog .session-rename-input")
    rename_input.send_keys(Keys.CONTROL, "a")
    rename_input.send_keys(renamed)
    rename_input.send_keys(Keys.ENTER)
    WebDriverWait(api_sweep_browser.driver, 12).until(
        lambda driver: driver.execute_script(
            "return sessions.includes(arguments[0]) && !sessions.includes(arguments[1]) "
            "&& Boolean(document.querySelector(`.dockview-pane-tab[data-pane-tab=\"${CSS.escape(arguments[0])}\"]`));",
            renamed,
            session,
        )
    )

    api_sweep_browser.driver.execute_async_script(
        "requestAnimationFrame(() => requestAnimationFrame(arguments[arguments.length - 1]));"
    )
    observations = api_sweep_browser.api_journey_observations()
    # No allowlist: a normal authenticated journey owns nothing outside 2xx.
    evidence = assert_api_journey_error_free(observations)
    print(f"API journey evidence: {json.dumps(evidence, sort_keys=True)}")
    assert evidence["observedResponseCount"] > 0, evidence
    assert evidence["allowedNon2xx"] == [], evidence
    observed_routes = set(evidence["observedRoutes"])
    assert "POST /api/fs/batch" in observed_routes, evidence
    assert "GET /api/session-files" in observed_routes, evidence
    assert "POST /api/rename-session" in observed_routes, evidence

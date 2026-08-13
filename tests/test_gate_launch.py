# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Section R: create, launch, interact, reload, and tear down real sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

import pytest
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import load_gate_browser
from tests.gate_harness import run_when_browser_ready
from tests.serving_process import pid_is_serving
from tests.serving_process import process_group_has_serving_member
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from yolomux_lib.local_services.registry import process_state
from yolomux_lib.tmux.session_retirement import capture_tmux_session_retirement
from yolomux_lib.tmux.session_retirement import join_tmux_session_retirement
from yolomux_lib.tmux.session_retirement import retained_tmux_session_births
from yolomux_lib.tmux.sessions import discover_sessions


REPO_ROOT = Path(__file__).resolve().parents[1]
NEW_SESSION = "1"
CREATE_SAMPLE_COUNT = 3
CREATE_TRANSITION_MEASUREMENT_TIMEOUT_SECONDS = 8.0
CREATE_TRANSITION_BUDGET_SECONDS = 2.0
MOCK_READY_TEXT = {
    "claude": "Claude Code v",
    "codex": "OpenAI Codex (v",
}

# Every browser test in this module is marked individually so the one non-browser negative
# control can declare itself, matching tests/test_gate_editor.py.
pytestmark = pytest.mark.socket


def _write_mock_agent_wrapper(path: Path, agent: str) -> None:
    mock_path = REPO_ROOT / "tools" / "mockers" / f"{agent}.py"
    auth_args = ["auth", "status"] if agent == "claude" else ["login", "status"]
    auth_output = json.dumps({"loggedIn": True}) if agent == "claude" else "Logged in"
    path.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "from __future__ import annotations",
                "import os",
                "import sys",
                f"if sys.argv[1:] == {auth_args!r}:",
                f"    print({auth_output!r})",
                "    raise SystemExit(0)",
                f"os.execv(sys.executable, [sys.executable, {str(mock_path)!r}, '--mock', *sys.argv[1:]])",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def _prepare_launch_runtime(monkeypatch: pytest.MonkeyPatch, runtime) -> Path:
    """Expose deterministic mock executables and fixture-owned numbered workdirs."""

    bin_dir = runtime.paths.root / "mock-agent-bin"
    bin_dir.mkdir()
    for agent in MOCK_READY_TEXT:
        _write_mock_agent_wrapper(bin_dir / agent, agent)
    monkeypatch.setenv("YOLOMUX_EXTRA_PATH", str(bin_dir))
    monkeypatch.setenv("PATH", os.pathsep.join((str(bin_dir), os.environ["PATH"])))
    (runtime.paths.workspace_dir / f"project{NEW_SESSION}").mkdir()
    return bin_dir


def _create_session_in_browser(browser, agent: str, *, terminal: str = "") -> dict[str, object]:
    result = browser.execute_async_script(
        """
        const agent = arguments[0];
        const terminal = arguments[1];
        const target = arguments[2];
        const timeoutMs = arguments[3];
        const done = arguments[arguments.length - 1];
        const started = performance.now();
        const tabSelector = `.dockview-pane-tab[data-pane-tab="${CSS.escape(target)}"]`;
        const initial = {
          roster: sessions.includes(target),
          tab: Boolean(document.querySelector(tabSelector)),
        };
        const elapsedWhen = async (predicate, description) => {
          await window.__yolomuxTestWaitFor(predicate, {timeoutMs, description});
          return performance.now() - started;
        };
        const roster = elapsedWhen(() => sessions.includes(target), `${target} in browser roster`);
        const rendered = elapsedWhen(() => Boolean(document.querySelector(tabSelector)), `${target} rendered tab`);
        const options = agent === 'term' ? {terminal} : {};
        (async () => {
          try {
            await Promise.all([createNextSession(agent, options), roster, rendered]);
            done({
              initial,
              rosterMs: await roster,
              renderedMs: await rendered,
              roster: sessions.includes(target),
              tab: Boolean(document.querySelector(tabSelector)),
              terminal: Boolean(document.querySelector(`#term-${CSS.escape(target)}`)),
            });
          } catch (error) {
            done({error: String(error?.stack || error), initial, status: document.getElementById('status')?.textContent || ''});
          }
        })();
        """,
        agent,
        terminal,
        NEW_SESSION,
        int(CREATE_TRANSITION_MEASUREMENT_TIMEOUT_SECONDS * 1000),
    )
    assert "error" not in result, result
    assert result["initial"] == {"roster": False, "tab": False}, result
    return result


def _kill_session_in_browser(browser) -> dict[str, object]:
    result = browser.execute_async_script(
        """
        const target = arguments[0];
        const done = arguments[arguments.length - 1];
        const originalConfirm = window.confirm;
        window.confirm = () => true;
        (async () => {
          try {
            const killed = await killTmuxSession(target);
            await window.__yolomuxTestWaitFor(() => (
              !sessions.includes(target)
              && !document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(target)}"]`)
              && !document.getElementById(`panel-${target}`)
            ), {timeoutMs: 5000, description: `${target} removed after kill`});
            done({
              killed,
              roster: sessions.includes(target),
              tab: Boolean(document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(target)}"]`)),
              panel: Boolean(document.getElementById(`panel-${target}`)),
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          } finally {
            window.confirm = originalConfirm;
          }
        })();
        """,
        NEW_SESSION,
    )
    assert "error" not in result, result
    assert result == {"killed": True, "panel": False, "roster": False, "tab": False}, result
    return result


def _assert_agent_detected(browser, runtime, agent: str) -> None:
    ready, panes = wait_for_isolated_tmux_panes(
        runtime.tmux,
        (NEW_SESSION,),
        lambda captures: MOCK_READY_TEXT[agent] in captures.get(NEW_SESSION, ""),
        timeout=15,
    )
    assert ready, panes

    discovered, errors = discover_sessions([NEW_SESSION], enrich_paths=False)
    agents = discovered[NEW_SESSION].agents
    assert errors == [], errors
    assert len(agents) == 1, agents
    assert agents[0].kind == agent, agents[0]
    assert f"{agent}.py" in agents[0].command and "--mock" in agents[0].command, agents[0]

    # A forced metadata read is answered from the server's cache, so its bytes always predate the
    # request. Await the generation the server says will observe this instant, and assert the
    # rendered kind came from that generation -- the watchdog stays fail-closed but is no longer
    # what decides the test, so a build that never ran cannot look like one that was merely slow.
    rendered = browser.execute_async_script(
        """
        const target = arguments[0];
        const agent = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          const baseline = Number(transcriptMetadataState.generation || 0);
          try {
            const forced = await refreshSessionMetadata({force: true, refreshAuto: true, refreshActivity: true});
            const awaited = Number(transcriptMetadataState.pendingGeneration || 0);
            await window.__yolomuxTestWaitFor(() => (
              Number(transcriptMetadataState.generation || 0) >= awaited
              && sessionAgentKind(target) === agent
            ), {
              timeoutMs: 15000,
              description: `${target} rendered as ${agent} from metadata generation >= ${awaited}`,
            });
            done({
              kind: sessionAgentKind(target),
              roster: sessions.includes(target),
              baseline,
              awaited,
              generation: Number(transcriptMetadataState.generation || 0),
              // The forced read's own typed outcome and the apply it produced. `awaited` is shared
              // state, so when it is zero these name WHICH side dropped the promised identity: the
              // server named none, or this client refused the payload that carried it.
              forced,
              lastApply: transcriptMetadataState.lastApply,
            });
          } catch (error) {
            // Name which side stalled: the server never built the generation it promised, or it
            // built it and the browser never applied it.
            let served = null;
            try {
              const probe = await apiFetchJson('/api/session-metadata');
              served = {generation: Number(probe?.metadata_generation || 0), cache: probe?.cache || null};
            } catch (probeError) {
              served = {error: String(probeError)};
            }
            done({
              error: String(error?.stack || error),
              kind: sessionAgentKind(target),
              baseline,
              awaited: Number(transcriptMetadataState.pendingGeneration || 0),
              generation: Number(transcriptMetadataState.generation || 0),
              lastApply: transcriptMetadataState.lastApply,
              served,
            });
          }
        })();
        """,
        NEW_SESSION,
        agent,
    )
    evidence = json.dumps(rendered, sort_keys=True, default=str)
    assert "error" not in rendered, evidence
    assert rendered["kind"] == agent and rendered["roster"] is True, evidence
    assert rendered["awaited"] > rendered["baseline"], evidence
    assert rendered["generation"] >= rendered["awaited"], evidence


def _pane_process_group(tmux_runtime) -> tuple[int, int]:
    result = run_isolated_tmux(tmux_runtime, "display-message", "-p", "-t", f"{NEW_SESSION}:", "#{pane_pid}")
    assert result.returncode == 0, result.stderr or result.stdout
    pane_pid = int(result.stdout.strip())
    return pane_pid, os.getpgid(pane_pid)


def _start_foreign_socket_session(tmux_runtime, socket_path: Path) -> None:
    """Start a same-named session on a second tmux socket the fixture must never touch."""

    started = subprocess.run(
        (tmux_runtime.tmux_binary, "-S", str(socket_path), "new-session", "-d", "-s", NEW_SESSION),
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert started.returncode == 0, started.stderr or started.stdout


def _start_ready_retirement_session(tmux_runtime) -> None:
    """Launch a deterministic long-lived process and wait for its explicit READY."""

    source = "import signal; print('R6_RETIREMENT_READY', flush=True); signal.pause()"
    created = run_isolated_tmux(
        tmux_runtime,
        "new-session",
        "-d",
        "-s",
        NEW_SESSION,
        sys.executable,
        "-u",
        "-c",
        source,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    ready, panes = wait_for_isolated_tmux_panes(
        tmux_runtime,
        (NEW_SESSION,),
        lambda captures: "R6_RETIREMENT_READY" in captures.get(NEW_SESSION, ""),
        timeout=5,
    )
    assert ready, panes


def _foreign_socket_has_session(tmux_runtime, socket_path: Path) -> int:
    return subprocess.run(
        (tmux_runtime.tmux_binary, "-S", str(socket_path), "has-session", "-t", f"{NEW_SESSION}:"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    ).returncode


def _process_group_exists(process_group_id: int) -> bool:
    """Report a group alive only while it holds a live, non-zombie member.

    A raw ``os.killpg(pgid, 0)`` counts a zombie that still retains the PGID as a
    live member, so under full-gate contention the transient zombie window after a
    kill outlives R6's tolerance and a dead group reads as alive. Route the oracle
    through the shared serving-member predicate, which excludes zombies exactly as
    production's ``bounded_process_table`` does.
    """

    return process_group_has_serving_member(process_group_id)


@pytest.mark.browser
def test_r1_create_session_reaches_roster_and_rendered_tab_within_measured_budget(browser, gate_live_server):
    (gate_live_server.paths.workspace_dir / f"project{NEW_SESSION}").mkdir()
    load_gate_browser(browser, gate_live_server)
    elapsed_samples: list[float] = []

    for sample in range(CREATE_SAMPLE_COUNT):
        result = _create_session_in_browser(browser, "term", terminal="bash")
        assert result["roster"] is True and result["tab"] is True and result["terminal"] is True, result
        elapsed_samples.append(max(float(result["rosterMs"]), float(result["renderedMs"])) / 1000.0)
        if sample + 1 < CREATE_SAMPLE_COUNT:
            _kill_session_in_browser(browser)

    ordered = sorted(elapsed_samples)
    measurements = {
        "samples_seconds": elapsed_samples,
        "median_seconds": statistics.median(elapsed_samples),
        "p95_nearest_rank_seconds": ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)],
        "max_seconds": max(elapsed_samples),
        "budget_seconds": CREATE_TRANSITION_BUDGET_SECONDS,
    }
    print(f"R1 baseline: {measurements}")
    assert max(elapsed_samples) <= CREATE_TRANSITION_BUDGET_SECONDS, measurements


@pytest.mark.browser
def test_r2_launch_claude_mock_is_live_and_detected_as_claude(browser, gate_live_server, monkeypatch):
    _prepare_launch_runtime(monkeypatch, gate_live_server)
    load_gate_browser(browser, gate_live_server)
    created = _create_session_in_browser(browser, "claude")
    assert created["terminal"] is True, created
    _assert_agent_detected(browser, gate_live_server, "claude")


@pytest.mark.browser
def test_r3_launch_codex_mock_is_live_and_detected_as_codex(browser, gate_live_server, monkeypatch):
    _prepare_launch_runtime(monkeypatch, gate_live_server)
    load_gate_browser(browser, gate_live_server)
    created = _create_session_in_browser(browser, "codex")
    assert created["terminal"] is True, created
    _assert_agent_detected(browser, gate_live_server, "codex")


@pytest.mark.browser
def test_r4_browser_xterm_input_receives_mock_output(browser, gate_live_server, monkeypatch):
    _prepare_launch_runtime(monkeypatch, gate_live_server)
    load_gate_browser(browser, gate_live_server)
    _create_session_in_browser(browser, "claude")
    _assert_agent_detected(browser, gate_live_server, "claude")
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script(
            "return Boolean(document.querySelector(`#term-${arguments[0]} textarea`)"
            " && terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN);",
            NEW_SESSION,
        )
    )

    browser.find_element("css selector", f'.dockview-pane-tab[data-pane-tab="{NEW_SESSION}"]').click()
    terminal_screen = WebDriverWait(browser, 8).until(
        lambda driver: driver.find_element("css selector", f"#term-{NEW_SESSION} .xterm-screen")
    )
    terminal_screen.click()
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script(
            "return document.activeElement === document.querySelector(`#term-${arguments[0]} textarea`);",
            NEW_SESSION,
        )
    )
    ActionChains(browser).send_keys("/status").send_keys(Keys.ENTER).perform()
    observed, panes = wait_for_isolated_tmux_panes(
        gate_live_server.tmux,
        (NEW_SESSION,),
        lambda captures: "Session status" in captures.get(NEW_SESSION, "") and "Tokens out:" in captures.get(NEW_SESSION, ""),
        timeout=5,
    )
    assert observed, panes


@pytest.mark.browser
def test_r5_reload_preserves_session_attachment_and_agent_detection(browser, gate_live_server, monkeypatch):
    _prepare_launch_runtime(monkeypatch, gate_live_server)
    load_gate_browser(browser, gate_live_server)
    _create_session_in_browser(browser, "claude")
    _assert_agent_detected(browser, gate_live_server, "claude")

    browser.refresh()
    attachment = run_when_browser_ready(
        browser,
        """
        return {
          roster: sessions.includes(arguments[0]),
          tab: Boolean(document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(arguments[0])}"]`)),
          terminal: Boolean(document.querySelector(`#term-${CSS.escape(arguments[0])} textarea`)),
          socketOpen: terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN,
        };
        """,
        NEW_SESSION,
        globals_required={"sessionAgentKind": "function", "refreshSessionMetadata": "function"},
        dom_anchors=("#grid",),
        timeout=8,
    )
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script(
            "return terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN;",
            NEW_SESSION,
        )
    )
    attachment["socketOpen"] = browser.execute_script(
        "return terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN;",
        NEW_SESSION,
    )
    assert attachment == {"roster": True, "socketOpen": True, "tab": True, "terminal": True}, attachment
    _assert_agent_detected(browser, gate_live_server, "claude")


@pytest.mark.browser
def test_r6_kill_removes_row_and_process_group_without_touching_another_socket(browser, gate_live_server, monkeypatch):
    _prepare_launch_runtime(monkeypatch, gate_live_server)
    load_gate_browser(browser, gate_live_server)
    _create_session_in_browser(browser, "claude")
    _assert_agent_detected(browser, gate_live_server, "claude")
    pane_pid, process_group_id = _pane_process_group(gate_live_server.tmux)
    assert pane_pid > 1 and process_group_id > 1
    retirement_identity = capture_tmux_session_retirement(NEW_SESSION)

    foreign_socket = gate_live_server.tmux.socket_dir / "foreign-s"
    _start_foreign_socket_session(gate_live_server.tmux, foreign_socket)
    try:
        _kill_session_in_browser(browser)
        join_tmux_session_retirement(retirement_identity, timeout=0)
        assert run_isolated_tmux(gate_live_server.tmux, "has-session", "-t", f"{NEW_SESSION}:").returncode != 0
        assert _foreign_socket_has_session(gate_live_server.tmux, foreign_socket) == 0
    finally:
        subprocess.run(
            (gate_live_server.tmux.tmux_binary, "-S", str(foreign_socket), "kill-server"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )


@pytest.mark.no_browser
def test_r6_kill_invariants_reject_a_surviving_group_and_a_killed_foreign_socket(gate_tmux):
    """Negative control for R6: each kill invariant must take BOTH values on real processes.

    R6 only protects anything if its three predicates can fail. A green R6 whose predicates are
    vacuous -- a process group that always reads dead, a foreign socket that always reads alive --
    would report the exact same result while a kill leaked across sockets. Drive each predicate
    through both states directly, without the browser, so the assertions R6 relies on are proven
    discriminating rather than merely satisfied.
    """

    _start_ready_retirement_session(gate_tmux)
    pane_pid, process_group_id = _pane_process_group(gate_tmux)
    assert pane_pid > 1 and process_group_id > 1
    retirement_identity = capture_tmux_session_retirement(NEW_SESSION)

    foreign_socket = gate_tmux.socket_dir / "negative-control-s"
    _start_foreign_socket_session(gate_tmux, foreign_socket)
    try:
        # Live state: every predicate reports the session and its process group as present.
        assert _process_group_exists(process_group_id) is True
        assert retained_tmux_session_births(retirement_identity)
        assert run_isolated_tmux(gate_tmux, "has-session", "-t", f"{NEW_SESSION}:").returncode == 0
        assert _foreign_socket_has_session(gate_tmux, foreign_socket) == 0

        # A kill scoped to the fixture socket must be visible in both fixture-socket predicates.
        killed = run_isolated_tmux(gate_tmux, "kill-session", "-t", f"{NEW_SESSION}:")
        assert killed.returncode == 0, killed.stderr or killed.stdout
        join_tmux_session_retirement(retirement_identity)
        assert retained_tmux_session_births(retirement_identity) == ()
        assert run_isolated_tmux(gate_tmux, "has-session", "-t", f"{NEW_SESSION}:").returncode != 0

        # The foreign-socket predicate is the one that catches a kill leaking to another server.
        # Prove it reports the violation by killing that server and reading it again.
        subprocess.run(
            (gate_tmux.tmux_binary, "-S", str(foreign_socket), "kill-server"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert _foreign_socket_has_session(gate_tmux, foreign_socket) != 0
    finally:
        subprocess.run(
            (gate_tmux.tmux_binary, "-S", str(foreign_socket), "kill-server"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )


@pytest.mark.no_browser
@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_r6_process_group_oracle_reports_a_zombie_only_group_as_dead():
    """A group whose only member is an unreaped zombie must read as NOT surviving.

    This is the exact class that reddens R6 under load: after a kill the tmux pane
    briefly lingers as a zombie that keeps its PGID, so the retired ``killpg(pgid,
    0)`` oracle reads the dead group as alive. Forge that state directly -- a child
    made its own group leader that exits without being reaped -- and prove the old
    oracle mis-reads it while the shared serving-member predicate does not. Then add
    a genuinely live member to the same group and prove the predicate still reports
    it, so a real survivor cannot be ignored.
    """

    zombie_pid = os.fork()
    if zombie_pid == 0:  # pragma: no cover - child never returns
        os.setpgid(0, 0)
        os._exit(0)
    live_pid = 0
    try:
        os.setpgid(zombie_pid, zombie_pid)
        group_id = zombie_pid
        deadline = time.monotonic() + 2.0
        while process_state(zombie_pid) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process_state(zombie_pid) == "Z", process_state(zombie_pid)

        # Red for the retired oracle: the zombie retains the PGID, so a raw group
        # probe reports the dead group as alive.
        raw_killpg_alive = True
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            raw_killpg_alive = False
        assert raw_killpg_alive is True

        # Green for the shared predicate: a zombie-only group is not surviving.
        assert process_group_has_serving_member(group_id) is False
        assert _process_group_exists(group_id) is False

        # Fails closed: a genuinely live member of the same group must still read
        # as surviving, so the fix ignores zombies, never live survivors.
        live_pid = os.fork()
        if live_pid == 0:  # pragma: no cover - child never returns
            os.setpgid(0, group_id)
            signal.pause()
            os._exit(0)
        os.setpgid(live_pid, group_id)
        settle = time.monotonic() + 2.0
        while pid_is_serving(live_pid) is False and time.monotonic() < settle:
            time.sleep(0.01)
        assert pid_is_serving(live_pid) is True
        assert process_group_has_serving_member(group_id) is True
        assert _process_group_exists(group_id) is True
    finally:
        if live_pid:
            os.kill(live_pid, signal.SIGKILL)
            os.waitpid(live_pid, 0)
        os.waitpid(zombie_pid, 0)

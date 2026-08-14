from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
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
from tests.gate_harness import wait_for_browser_boot
from tests.gate_harness import wait_for_fixture_api_quiescence
from tests.gate_harness import wait_for_fixture_client_event_demand
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.approval import approvald
from yolomux_lib.infra import jobd
from yolomux_lib.local_services.rpc import safe_socket_path
from yolomux_lib import statusd_client
from yolomux_lib.approval.prompt_detector import agent_screen_state
from yolomux_lib.tmux.sessions import discover_sessions


REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "prompt_corpus"
WORKING_CAPTURE = CAPTURE_ROOT / "captures" / "working_visible_counter__claude-code-2.1.183_20260620.yaml"
IDLE_CAPTURE = CAPTURE_ROOT / "synthetic" / "try_suggestion_idle__claude-code-synthetic_20260620.yaml"
ACHIEVED_CAPTURE = CAPTURE_ROOT / "captures" / "goal_achieved_idle_draft__codex-cli-0.141.0_20260624.yaml"
REALISTIC_SESSION_COUNT = 14
INITIAL_REALISTIC_SESSION_COUNT = 12
AGENT_STATE_GLOBALS = {
    "agentWindowSnapshotRevision": "function",
    "debugGraphLiveAgentWindowDetailHtml": "function",
    "refreshSessionMetadata": "function",
    "sessionAgentWindowStatusModel": "function",
}

GOAL_BLOCKED_CAPTURE = """• I could not finish the requested change.

  The fixture-owned daemon rejected the transition and no safe recovery remains.

› Explain this codebase

  gpt-5.5 xhigh · /fixture/worktree                    Goal blocked (21m)
"""


def _fixture_capture(path: Path) -> str:
    marker = "raw_capture: |\n"
    text = path.read_text(encoding="utf-8")
    assert marker in text, path
    capture = text.split(marker, 1)[1]
    lines = []
    for line in capture.splitlines():
        if line and not line.startswith("  "):
            break
        lines.append(line[2:] if line.startswith("  ") else "")
    return "\n".join(lines).rstrip() + "\n"


def _classified(capture: str, pane_target: str, now: float) -> tuple[dict[str, object], str]:
    screen = dict(agent_screen_state(capture, pane_target=pane_target, now=now))
    return screen, TmuxWebtermApp.agent_window_state_from_screen(screen)


def _browser_transition(browser, screens_and_states: list[tuple[dict[str, object], str]]) -> list[dict[str, object]]:
    return browser.execute_script(
        """
        const transitions = arguments[0];
        const session = '1';
        const snapshots = [];
        for (const [screen, state] of transitions) {
          const result = applyAutoApprovePayload({
            agent_window_snapshot_revision: snapshots.length + 101,
            session_order: [session],
            sessions: {
              [session]: {
                target: session,
                enabled: false,
                screen,
                agent_windows: [{
                  kind: 'claude',
                  state,
                  window_index: 0,
                  pane_target: '%gate-agent',
                  current: true,
                  window_active: true,
                }],
              },
            },
          });
          const agent = sessionAgentWindowStatusPayloads(session, {agents: [], panes: []})[0];
          const host = document.createElement('div');
          host.innerHTML = agentWindowActivityIconHtmlForStatus(agent, agent.kind, session, {statusOnly: true});
          const indicator = host.firstElementChild;
          snapshots.push({
            applied: result?.applied === true,
            state: agent?.state || '',
            classes: indicator?.className || '',
            label: indicator?.getAttribute('aria-label') || '',
            sessionState: sessionState(session, {agents: [], panes: []}).key,
          });
        }
        return snapshots;
        """,
        [[screen, state] for screen, state in screens_and_states],
    )


@pytest.mark.browser
def test_f1_working_to_idle_and_blocked_clears_the_rendered_green_indicator(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    working = _classified(_fixture_capture(WORKING_CAPTURE), "%f1-idle", 100.0)
    idle = _classified(_fixture_capture(IDLE_CAPTURE), "%f1-idle", 101.0)
    working_again = _classified(_fixture_capture(WORKING_CAPTURE), "%f1-blocked", 200.0)
    blocked = _classified(GOAL_BLOCKED_CAPTURE, "%f1-blocked", 201.0)

    idle_transition = _browser_transition(browser, [working, idle])
    blocked_transition = _browser_transition(browser, [working_again, blocked])

    assert "agent-window-activity--working" in idle_transition[0]["classes"], idle_transition
    assert idle_transition[1]["state"] != "working", idle_transition
    assert "agent-window-activity--working" not in idle_transition[1]["classes"], idle_transition
    assert "agent-window-activity--working" in blocked_transition[0]["classes"], blocked_transition
    assert blocked_transition[1]["state"] != "working", blocked_transition
    assert "agent-window-activity--working" not in blocked_transition[1]["classes"], blocked_transition


@pytest.mark.browser
def test_f2_active_working_capture_renders_the_green_indicator(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    working = _classified(_fixture_capture(WORKING_CAPTURE), "%f2-working", 300.0)

    [rendered] = _browser_transition(browser, [working])

    assert working[0]["key"] == "working", working
    assert working[1] == "working", working
    assert rendered["state"] == "working", rendered
    assert "agent-window-activity--working" in rendered["classes"], rendered


@pytest.mark.browser
def test_f3_goal_blocked_renders_distinctly_from_goal_achieved(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    achieved = _classified(_fixture_capture(ACHIEVED_CAPTURE), "%f3-achieved", 400.0)
    blocked = _classified(GOAL_BLOCKED_CAPTURE, "%f3-blocked", 401.0)

    [achieved_rendered] = _browser_transition(browser, [achieved])
    [blocked_rendered] = _browser_transition(browser, [blocked])

    assert blocked[0]["key"] == "blocked", {"achieved": achieved, "blocked": blocked}
    assert blocked[1] == "blocked", {"achieved": achieved, "blocked": blocked}
    assert blocked_rendered["sessionState"] == "blocked", blocked_rendered
    assert blocked_rendered != achieved_rendered, {"achieved": achieved_rendered, "blocked": blocked_rendered}


@pytest.mark.socket
@pytest.mark.browser
def test_f4_python3_claude_mock_process_is_recognised_as_an_ai_agent(gate_tmux, gate_runtime_paths):
    session = gate_tmux.sessions[0]
    command = shlex.join([sys.executable, str(REPO_ROOT / "tools" / "mockers" / "claude.py"), "--mock"])
    launched = run_isolated_tmux(gate_tmux, "send-keys", "-t", f"{session}:", f"exec {command}", "Enter", timeout=5)
    assert launched.returncode == 0, launched.stderr or launched.stdout
    ready, panes = wait_for_isolated_tmux_panes(
        gate_tmux,
        [session],
        lambda captures: "Claude Code v" in captures[session] and 'Try "fix typecheck errors"' in captures[session],
        timeout=15,
    )
    assert ready, panes

    discovered, errors = discover_sessions([session], enrich_paths=False)
    agents = discovered[session].agents
    assert errors == [], errors
    assert len(agents) == 1, agents
    assert agents[0].kind == "claude", agents[0]
    assert "claude.py" in agents[0].command and "--mock" in agents[0].command, agents[0]


def _wait_for_mock_agents(runtime, sessions: list[str]) -> None:
    ready, panes = wait_for_isolated_tmux_panes(
        runtime.tmux,
        sessions,
        lambda captures: all("Claude Code v" in captures[session] for session in sessions),
        timeout=20,
    )
    assert ready, panes


def _launch_mock_agents(runtime, sessions: list[str]) -> None:
    command = shlex.join([sys.executable, str(REPO_ROOT / "tools" / "mockers" / "claude.py"), "--mock"])
    for session in sessions:
        launched = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", f"exec {command}", "Enter", timeout=5)
        assert launched.returncode == 0, launched.stderr or launched.stdout
    _wait_for_mock_agents(runtime, sessions)


def _add_mock_agent_sessions(runtime, count: int) -> list[str]:
    stem = runtime.sessions[0].rsplit("-", 1)[0]
    sessions = [f"{stem}-{index}" for index in range(len(runtime.sessions) + 1, len(runtime.sessions) + count + 1)]
    command = shlex.join([sys.executable, str(REPO_ROOT / "tools" / "mockers" / "claude.py"), "--mock"])
    for session in sessions:
        created = run_isolated_tmux(
            runtime.tmux,
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            "120",
            "-y",
            "36",
            command,
            timeout=10,
        )
        assert created.returncode == 0, created.stderr or created.stdout
    runtime.sessions.extend(sessions)
    _wait_for_mock_agents(runtime, sessions)
    runtime.tmux.sessions.extend(sessions)
    return sessions


def _wait_for_realistic_browser_roster(browser, sessions: list[str]) -> dict[str, object]:
    metrics = browser.execute_async_script(
        """
        const expected = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const metadataSessions = transcriptMetadataState.payload?.sessions || {};
          return sessions.length === expected.length
            && expected.every(session => metadataSessions[session])
            && expected.every(session => (autoApproveStates.get(session)?.agent_windows || []).length === 1)
            && expected.every(session => agentWindowSnapshotRevision(autoApproveStates.get(session)) > 0);
        }, {timeoutMs: 20000, description: `${expected.length}-session producer roster`}).then(() => done({
          sessionCount: sessions.length,
          metadataCount: Object.keys(transcriptMetadataState.payload?.sessions || {}).length,
          revisions: expected.map(session => agentWindowSnapshotRevision(autoApproveStates.get(session))),
          rowCount: expected.reduce((count, session) => count + (autoApproveStates.get(session)?.agent_windows || []).length, 0),
        }), error => done({error: String(error?.stack || error)}));
        """,
        sessions,
    )
    assert not metrics.get("error"), metrics
    assert metrics["sessionCount"] == metrics["metadataCount"] == len(sessions), metrics
    assert metrics["rowCount"] == len(sessions), metrics
    assert all(revision > 0 for revision in metrics["revisions"]), metrics
    return metrics


def _assert_runtime_owns_durable_and_service_paths(runtime) -> None:
    """Keep fixture apps off the process-wide local-service sockets and state."""

    paths = runtime.paths
    assert runtime.app.chat_store.path.is_relative_to(paths.state_dir)
    assert runtime.app.status_client.socket_path == safe_socket_path(
        paths.runtime_dir / "services" / statusd_client.STATUSD_SOCKET_NAME,
        prefix="yolomux-statusd",
    )
    assert runtime.app.job_client.socket_path == safe_socket_path(
        paths.runtime_dir / "services" / jobd.JOBD_SOCKET_NAME,
        prefix="yolomux-jobd",
    )
    assert runtime.app.approval_client.socket_path == safe_socket_path(
        paths.runtime_dir / "services" / approvald.APPROVALD_SOCKET_NAME,
        prefix="yolomux-approvald",
    )


def _warm_realistic_status_snapshot(runtime) -> None:
    refresh_errors = runtime.app.refresh_sessions(maintenance=False)
    assert refresh_errors == [], refresh_errors
    assert set(runtime.app.sessions) == set(runtime.sessions), {
        "appSessions": runtime.app.sessions,
        "fixtureSessions": runtime.sessions,
    }
    response, body = runtime.app.status_client.snapshot(runtime.app.sessions, timeout=1.0)
    if response.get("ok") is not True:
        waited = runtime.app.status_client.wait_generation(0, timeout=20.0)
        assert waited.get("ok") is True and int(waited.get("generation") or 0) > 0, waited
        response, body = runtime.app.status_client.snapshot(runtime.app.sessions, timeout=1.0)
    assert response.get("ok") is True and body, response


@contextmanager
def _realistic_agent_browser_runtime(browser, monkeypatch, gate_runtime_paths, session_count: int):
    runtime = start_isolated_browser_app(
        monkeypatch,
        gate_runtime_paths.root,
        session_count=session_count,
        dangerously_yolo=False,
    )
    server = thread = None
    try:
        _assert_runtime_owns_durable_and_service_paths(runtime)
        assert runtime.paths.config_dir.parent == gate_runtime_paths.root
        assert runtime.paths.state_dir.parent == gate_runtime_paths.root
        _launch_mock_agents(runtime, runtime.sessions)
        server, thread = start_browser_server(
            monkeypatch,
            gate_runtime_paths.config_dir,
            runtime.app,
            auth_bypass=True,
        )
        _warm_realistic_status_snapshot(runtime)
        session = runtime.sessions[0]
        # Keep YO!info active so the production browser demand includes the transcripts SSE
        # channel; F6 then exercises the event-driven metadata refresh rather than a test call.
        query = urlencode({"debug": "1", "sessions": session, "layout": "left", "tabs": f"left:{session},__info__*"})
        browser.get(f"http://127.0.0.1:{server.server_address[1]}/?{query}")
        assert_live_runtime_boot_healthy(browser, "regression-gate", timeout=20)
        wait_for_browser_boot(browser, globals_required=AGENT_STATE_GLOBALS, dom_anchors=("#grid",), timeout=20)
        wait_for_fixture_client_event_demand(browser)
        _wait_for_realistic_browser_roster(browser, runtime.sessions)
        wait_for_fixture_api_quiescence(browser, timeout=20)
        WebDriverWait(browser, 10).until(
            lambda _driver: (
                runtime.app.client_watch_service.event_watcher_record.status_generation_worker is not None
                and runtime.app.client_watch_service.event_watcher_record.status_generation_worker.is_alive()
            )
        )
        yield SimpleNamespace(browser=browser, runtime=runtime, server=server)
    finally:
        if server is not None and thread is not None:
            stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)


@pytest.mark.browser
def test_f5_realistic_roster_count_matches_its_stale_session_breakdown(
    browser, monkeypatch, gate_runtime_paths
):
    with _realistic_agent_browser_runtime(
        browser, monkeypatch, gate_runtime_paths, REALISTIC_SESSION_COUNT
    ) as fixture:
        rendered = fixture.browser.execute_script(
            """
            const revision = Math.max(...sessions.map(session => agentWindowSnapshotRevision(autoApproveStates.get(session))));
            jsDebugStatsPollState.agentWindowSnapshotRevision = revision + 2;
            const host = document.createElement('div');
            host.innerHTML = debugGraphLiveAgentWindowDetailHtml('activity');
            const detail = host.firstElementChild;
            return {
              revision,
              text: detail?.textContent || '',
              state: detail?.dataset.jsDebugAgentWindowDetailState || '',
              stale: [...host.querySelectorAll('.js-debug-agent-window-detail-stale')].map(node => node.textContent),
              fetchPaths: performance.getEntriesByType('resource').map(entry => new URL(entry.name).pathname),
            };
            """
        )

    assert rendered["state"] == "stale", rendered
    assert rendered["revision"] > 0, rendered
    assert "/api/session-metadata" in rendered["fetchPaths"] and "/api/auto-approve" in rendered["fetchPaths"], rendered
    assert f"{REALISTIC_SESSION_COUNT} agent windows across {REALISTIC_SESSION_COUNT} sessions" in rendered["text"], rendered
    assert len(rendered["stale"]) == 1, rendered
    assert rendered["stale"][0].count(" status is stale ") == REALISTIC_SESSION_COUNT, rendered


@pytest.mark.browser
def test_f6_realistic_consumers_converge_to_the_published_roster_revision(
    browser, monkeypatch, gate_runtime_paths
):
    with _realistic_agent_browser_runtime(
        browser, monkeypatch, gate_runtime_paths, INITIAL_REALISTIC_SESSION_COUNT
    ) as fixture:
        initial = _wait_for_realistic_browser_roster(fixture.browser, fixture.runtime.sessions)
        initial_revision = max(initial["revisions"])
        added = _add_mock_agent_sessions(fixture.runtime, REALISTIC_SESSION_COUNT - INITIAL_REALISTIC_SESSION_COUNT)
        expected = sorted(fixture.runtime.sessions)
        metadata_requests_before = fixture.browser.execute_script(
            "return performance.getEntriesByType('resource').filter(entry => new URL(entry.name).pathname === '/api/session-metadata').length;"
        )
        WebDriverWait(fixture.browser, 10).until(
            lambda _driver: (
                fixture.runtime.app.client_watch_service.event_watcher_record.snapshot_worker is None
                and fixture.runtime.app.activity_transcript_service.transcripts_payload_cache_record.worker is None
            )
        )
        # This is the production ownership chain for roster updates: the server's watch-snapshot
        # scheduler builds and caches session metadata, publishes a transcripts_changed SSE nudge,
        # and the browser then requests and consumes that published cache. Calling
        # refreshSessionMetadata here would bypass the scheduler and could make an empty producer
        # payload look healthy by directly refreshing the consumer.
        assert fixture.runtime.app.start_client_watch_snapshot_publish() is True
        result = fixture.browser.execute_async_script(
            """
            const expected = arguments[0];
            const initialRevision = arguments[1];
            const metadataRequestsBefore = arguments[2];
            const done = arguments[arguments.length - 1];
            const observations = [];
            const snapshot = () => {
              const metadataSessions = transcriptMetadataState.payload?.sessions || {};
              const revisions = expected.map(session => agentWindowSnapshotRevision(autoApproveStates.get(session)));
              const models = expected.map(session => sessionAgentWindowStatusModel(session));
              const detailHost = document.createElement('div');
              detailHost.innerHTML = debugGraphLiveAgentWindowDetailHtml('activity');
              return {
                sessionCount: sessions.length,
                metadataCount: Object.keys(metadataSessions).length,
                statusRowCount: expected.reduce((count, session) => count + (autoApproveStates.get(session)?.agent_windows || []).length, 0),
                modelRowCount: models.reduce((count, model) => count + (model?.agents || []).length, 0),
                revisions,
                detailState: detailHost.firstElementChild?.dataset.jsDebugAgentWindowDetailState || '',
                detailText: detailHost.textContent || '',
                metadataRequestCount: performance.getEntriesByType('resource').filter(entry => new URL(entry.name).pathname === '/api/session-metadata').length,
              };
            };
            const timer = setInterval(() => observations.push(snapshot()), 20);
            (async () => {
              try {
                await window.__yolomuxTestWaitFor(() => {
                  const current = snapshot();
                  return current.metadataRequestCount > metadataRequestsBefore
                    && current.sessionCount === expected.length
                    && current.metadataCount === expected.length
                    && current.statusRowCount === expected.length
                    && current.modelRowCount === expected.length
                    && current.revisions.every(revision => revision > initialRevision);
                }, {timeoutMs: 20000, description: `${expected.length}-session higher-revision consumer convergence`});
                observations.push(snapshot());
                done({initialRevision, final: snapshot(), observations});
              } catch (error) {
                done({error: String(error?.stack || error), initialRevision, final: snapshot(), observations});
              } finally {
                clearInterval(timer);
              }
            })();
            """,
            expected,
            initial_revision,
            metadata_requests_before,
        )
        scheduler = fixture.runtime.app.client_events.snapshot()

    published = scheduler["published_by_type"].get("auto_approve_changed", {"events": 0, "bytes": 0})
    metadata_published = scheduler["published_by_type"].get("transcripts_changed", {"events": 0, "bytes": 0})
    assert published["events"] > 0 and published["bytes"] > 0, scheduler
    assert metadata_published["events"] > 0 and metadata_published["bytes"] > 0, scheduler
    advanced = [
        observation
        for observation in result["observations"]
        if observation["revisions"] and all(revision > result["initialRevision"] for revision in observation["revisions"])
    ]
    assert advanced, result
    assert all(
        observation["sessionCount"] == observation["metadataCount"] == REALISTIC_SESSION_COUNT
        and observation["statusRowCount"] == observation["modelRowCount"] == REALISTIC_SESSION_COUNT
        for observation in advanced
    ), result
    assert not result.get("error"), result
    assert result["final"]["metadataRequestCount"] > metadata_requests_before, result
    assert result["final"]["sessionCount"] == result["final"]["metadataCount"] == REALISTIC_SESSION_COUNT, result
    assert result["final"]["statusRowCount"] == result["final"]["modelRowCount"] == REALISTIC_SESSION_COUNT, result
    assert all(revision > result["initialRevision"] for revision in result["final"]["revisions"]), result
    assert set(added).issubset(expected), {"added": added, "expected": expected}

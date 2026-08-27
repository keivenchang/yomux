from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shlex
import subprocess
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
from tests.tmux_runtime import create_isolated_tmux_session
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
# One owner for the failure-only diagnostic projection, shared by F6's failure path and by the
# reachability probe below, so the two can never drift into separate copies. Pure reads of the
# EXISTING `transcriptMetadataState.lastApply` owner and the existing deferred-payload binding:
# it writes nothing, adds no request/timer/retry, and is never evaluated on a passing wait.
F6_FAILURE_DIAGNOSTIC_JS = """
        function captureF6FailureDiagnostics() {
          const last = transcriptMetadataState.lastApply;
          const apply = (!last || typeof last !== 'object') ? null : {
            applied: last.applied === true,
            reason: String(last.reason || ''),
            payloadGeneration: Number(last.payloadGeneration || 0),
            appliedGeneration: Number(last.appliedGeneration || 0),
            session: last.session === undefined ? '' : String(last.session),
          };
          let deferred;
          try {
            deferred = deferredSealedAutoApprovePayload
              ? {
                  present: true,
                  revision: agentWindowSnapshotRevision(deferredSealedAutoApprovePayload),
                  sessionCount: Object.keys(deferredSealedAutoApprovePayload.sessions || {}).length,
                }
              : {present: false, revision: 0, sessionCount: 0};
          } catch (error) {
            deferred = {present: null, unreadable: String((error && error.name) || error)};
          }
          return {apply, deferred};
        }
"""

F6_CONVERGENCE_PREDICATE_JS = """
        function f6ConvergenceSatisfied(observation, expectedCount, initialRevision) {
          // Observable convergence only. The retired clause additionally required
          // `metadataRequestCount > metadataRequestsBefore`, which conditions success on
          // ONE transport: `transcripts_changed` carrying an inline payload applies
          // metadata directly through `applyTranscriptsPayload` -> `applySessionMetadataPayload`
          // and issues no HTTP request at all, so a fully converged client can leave that
          // count untouched forever. Measured twice on a frozen subject: baseline 2, final 2.
          if (!observation) return false;
          const revisions = observation.revisions;
          if (!Array.isArray(revisions) || revisions.length !== expectedCount) return false;
          return observation.sessionCount === expectedCount
            && observation.metadataCount === expectedCount
            && observation.statusRowCount === expectedCount
            && observation.modelRowCount === expectedCount
            && revisions.every(revision => revision > initialRevision);
        }
"""

F6_WAIT_FAILURE_SIGNATURE_FIELDS = (
    "sessionCount", "metadataCount", "statusRowCount", "modelRowCount",
    "revisions", "detailState", "detailText", "metadataRequestCount",
)


F6_OUTCOME_OWNER_JS = """
        function f6BuildOutcome(options) {
          // The single owner of every terminating shape, so the live script and the
          // regressions cannot drift into mirrored control flow.
          //
          // A failed wait ALWAYS reports `error`. If diagnostic capture or the final
          // snapshot then throws, that is recorded separately as `reportingError` - an
          // earlier revision let it fall into the success-side catch, which emitted
          // `postWaitError` with NO `error` key and silently lost the real wait failure.
          // `postWaitError` is therefore reserved for a reporting fault after a wait
          // that actually SUCCEEDED.
          const outcome = {
            initialRevision: options.initialRevision,
            observations: options.observations,
            diagnosticCaptures: 0,
            diagnostics: null,
            final: null,
            finalConverged: null,
          };
          let reportingError = null;
          if (options.waitFailure !== null && options.waitFailure !== undefined) {
            outcome.error = String(options.waitFailure.stack || options.waitFailure);
            try {
              outcome.diagnosticCaptures = 1;
              outcome.diagnostics = options.captureDiagnostics();
            } catch (error) {
              reportingError = error;
            }
          }
          try {
            outcome.final = options.takeSnapshot();
          } catch (error) {
            if (reportingError === null) reportingError = error;
          }
          try {
            outcome.finalConverged = outcome.final === null
              ? null
              : f6ConvergenceSatisfied(outcome.final, options.expectedCount, options.initialRevision);
          } catch (error) {
            if (reportingError === null) reportingError = error;
          }
          if (reportingError !== null) {
            const text = String(reportingError.stack || reportingError);
            if (outcome.error === undefined) outcome.postWaitError = text;
            else outcome.reportingError = text;
          }
          return outcome;
        }
"""

F6_RECORD_BUDGET = 1200
F6_STRING_BUDGETS = (240, 120, 60, 30)


def _f6_bounded_text(value, budget):
    """Bound ONE untrusted runtime string; report the original length when it is cut.

    Every string that can grow at runtime routes through here - the wait error, a
    reporting error, rendered detail text and state, and the string values nested inside
    the apply and deferred projections. Loss is reported, never silent.
    """

    if not isinstance(value, str) or len(value) <= budget:
        return value, None
    return value[:budget], len(value)


def _f6_bounded_mapping(mapping, budget):
    """Apply the one bounding owner to every string value of a projection."""

    if not isinstance(mapping, dict):
        return mapping
    bounded = {}
    for key, value in mapping.items():
        text, original = _f6_bounded_text(value, budget)
        bounded[key] = text
        if original is not None:
            bounded[f"{key}Truncated"] = True
            bounded[f"{key}Length"] = original
    return bounded


def _f6_first_line(value):
    text = str(value or "")
    return text.splitlines()[0] if text else ""


F6_DROP_ORDER = (
    ("final", "detailText"),
    ("final", "detailState"),
    ("apply", None),
    ("deferred", None),
    (None, "postWaitError"),
)


def _f6_drop_field(holder, key):
    """Remove ONE bounded string and the truncation metadata that described it."""

    for suffix in ("", "Truncated", "Length"):
        holder.pop(f"{key}{suffix}", None)


def _f6_surrender(record, steps):
    """The one owner that surrenders low-value bounded fields, and names what it took.

    Character budgets alone cannot enforce the record budget: JSON escaping expands a
    control character to six serialized characters, so even the smallest string budget can
    overflow. When that happens the fields in `F6_DROP_ORDER` are surrendered in order -
    rendered prose first, then the apply and deferred projections, then the post-wait
    error. The wait and reporting identities and the fixed-shape numbers are never on that
    list. Every surrendered name is listed in `droppedFields`, so a reader can always tell
    a surrendered field from one that was never present.
    """

    surrendered = []
    for scope, key in steps:
        if scope is None:
            _f6_drop_field(record, key)
            surrendered.append(key)
            continue
        holder = record.get(scope)
        if not isinstance(holder, dict):
            surrendered.append(f"{scope}.*" if key is None else f"{scope}.{key}")
            continue
        if key is None:
            for name in [k for k, v in holder.items() if isinstance(v, str)]:
                _f6_drop_field(holder, name)
            surrendered.append(f"{scope}.*")
        else:
            _f6_drop_field(holder, key)
            surrendered.append(f"{scope}.{key}")
    if surrendered:
        record["droppedFields"] = surrendered
    return record


def _f6_record_at_budget(result, metadata_requests_before, budget):
    diagnostics = result.get("diagnostics") or {}
    final = result.get("final") or {}
    record = {
        "diagnosticCaptures": result.get("diagnosticCaptures"),
        "apply": _f6_bounded_mapping(diagnostics.get("apply"), budget),
        "deferred": _f6_bounded_mapping(diagnostics.get("deferred"), budget),
        "initialRevision": result.get("initialRevision"),
        "metadataRequestsBefore": metadata_requests_before,
        "stringBudget": budget,
    }
    # The three outcome kinds stay SEPARATE. A failed wait reports `error`; a reporting
    # fault beside it reports `reportingError`; `postWaitError` means the wait SUCCEEDED
    # and only reporting failed. Collapsing them once lost a real wait failure entirely.
    for key in ("error", "reportingError", "postWaitError"):
        text, original = _f6_bounded_text(_f6_first_line(result.get(key)), budget)
        record[key] = text
        if original is not None:
            record[f"{key}Truncated"] = True
            record[f"{key}Length"] = original
    record["final"] = _f6_bounded_mapping(
        {key: final.get(key) for key in F6_WAIT_FAILURE_SIGNATURE_FIELDS}, budget,
    )
    return record


def f6_wait_failure_record(result, metadata_requests_before):
    """Render an F6 wait failure as one compact JSON line under a HARD size contract.

    A 20-second wait at a 20 ms cadence produces about a thousand observations, and
    asserting on the whole result made pytest elide the diagnostic itself. Bounding only
    the detail text was not enough: ANY runtime string can grow, so every one of them is
    bounded through a single owner and the serialized total is forced below the budget by
    shrinking that owner's limit until it fits. The applied limit is reported as
    `stringBudget`, so a reader can always tell a bounded record from a complete one.

    Character budgets are not sufficient on their own. JSON escaping expands a control
    character to six serialized characters, so the smallest string budget can still render
    over the record budget; the previous version returned that oversized record unchanged
    and unmarked, which is exactly the elision this helper exists to prevent. When the
    smallest budget still overflows, `_f6_surrender` gives up the lowest-value bounded
    fields in a fixed order and lists them under `droppedFields`.

    The return value is therefore ALWAYS shorter than `F6_RECORD_BUDGET`. If even the
    fixed-shape core plus the wait and reporting identities cannot fit, this raises rather
    than returning something a reader would take for complete; the message carries the
    measured size only, never the result it failed to render.

    `metadata_requests_before` is REQUIRED. A silent None default let a call site drop the
    baseline, and the baseline is what makes the recorded request count interpretable.
    """

    def render(budget, steps):
        record = _f6_record_at_budget(result, metadata_requests_before, budget)
        return json.dumps(_f6_surrender(record, steps), sort_keys=True)

    for budget in F6_STRING_BUDGETS:
        rendered = render(budget, ())
        if len(rendered) < F6_RECORD_BUDGET:
            return rendered

    smallest = F6_STRING_BUDGETS[-1]
    for depth in range(1, len(F6_DROP_ORDER) + 1):
        rendered = render(smallest, F6_DROP_ORDER[:depth])
        if len(rendered) < F6_RECORD_BUDGET:
            return rendered

    raise AssertionError(
        f"F6 record cannot meet the {F6_RECORD_BUDGET}-character contract: the fixed-shape "
        f"core with the wait and reporting identities serializes to {len(rendered)} "
        f"characters at string budget {smallest} after surrendering every droppable field"
    )


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
        create_isolated_tmux_session(
            runtime.tmux,
            session,
            columns=120,
            rows=36,
            command=command,
        )
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


@pytest.mark.no_browser
def test_f6_wait_failure_record_is_compact_json_that_survives_pytest_truncation():
    """The failure record must stay small enough to actually be read.

    The first real F6 failure ran the diagnostic correctly and pytest still elided
    `diagnostics.deferred` and `apply.reason`, because the assertion dumped the whole
    result including a 20-second, 20 ms-cadence `observations` array. A diagnostic that
    is captured and then truncated away is no diagnostic at all.
    """

    synthetic_observation = {
        "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
        "revisions": [7] * 14, "detailState": "changed",
        "detailText": "Live status is waiting for the chart snapshot",
        "metadataRequestCount": 2,
    }
    result = {
        "error": "Error: Timed out after 20000ms waiting for 14-session higher-revision"
                 " consumer convergence\n    at waitFor (<anonymous>:105:15)",
        "initialRevision": 7,
        "diagnosticCaptures": 1,
        "diagnostics": {
            "apply": {
                "applied": True, "reason": "applied",
                "payloadGeneration": 7, "appliedGeneration": 7, "session": "",
            },
            "deferred": {"present": True, "revision": 9, "sessionCount": 14},
        },
        "final": dict(synthetic_observation),
        # The array that caused the elision: 1000 observations, as a real 20s run produces.
        "observations": [dict(synthetic_observation) for _ in range(1000)],
    }

    record = f6_wait_failure_record(result, metadata_requests_before=2)

    assert isinstance(record, str), record
    parsed = json.loads(record)
    # Every apply and deferred field survives verbatim.
    assert parsed["apply"] == result["diagnostics"]["apply"], parsed
    assert parsed["deferred"] == {"present": True, "revision": 9, "sessionCount": 14}, parsed
    assert parsed["diagnosticCaptures"] == 1 and parsed["initialRevision"] == 7, parsed
    # The baseline the predicate compares against, so an impossible first clause is visible.
    assert parsed["metadataRequestsBefore"] == 2, parsed
    # No retired-clause label may appear: the live wait no longer contains that clause.
    assert "impossiblePredicate" not in parsed, parsed
    # Only the first error line, so a stack trace cannot crowd out the projection.
    assert parsed["error"].startswith("Error: Timed out after 20000ms"), parsed
    assert "\n" not in parsed["error"], parsed
    # The signature fields needed to compare against the preserved failures.
    assert parsed["final"]["metadataCount"] == 14 and parsed["final"]["metadataRequestCount"] == 2, parsed
    assert parsed["final"]["detailText"] == "Live status is waiting for the chart snapshot", parsed
    # The observations array and unrelated fixture state are gone.
    assert "observations" not in record, record
    assert "observations" not in parsed, parsed
    # Small enough that pytest will not elide it: the elided payload was far larger.
    assert len(record) < 1200, f"record is {len(record)} chars and risks truncation"


@pytest.mark.no_browser
def test_f6_wait_failure_record_reports_an_unreadable_deferred_binding():
    """An unreadable holder must be visible, not silently absent."""

    record = f6_wait_failure_record({
        "postWaitError": "ReferenceError: boom",
        "diagnosticCaptures": 0,
        "diagnostics": {"apply": None, "deferred": {"present": None, "unreadable": "ReferenceError"}},
    }, 2)
    parsed = json.loads(record)
    assert parsed["deferred"] == {"present": None, "unreadable": "ReferenceError"}, parsed
    assert parsed["apply"] is None, parsed
    # A reporting fault after a SUCCESSFUL wait is a post-wait fault and nothing else.
    assert parsed["postWaitError"] == "ReferenceError: boom", parsed
    assert parsed["error"] == "" and parsed["reportingError"] == "", parsed



def _f6_predicate_says(expression, observation, expected_count=14, initial_revision=1):
    """Evaluate an expression against the REAL shared predicate source, via node.

    The browser wait and this regression read the same `F6_CONVERGENCE_PREDICATE_JS`
    string, so there is no second Python copy that could pass here while the real wait
    diverges. Deterministic: no clock, no browser, no retry.
    """

    script = F6_CONVERGENCE_PREDICATE_JS + (
        f"\nconst observation = {json.dumps(observation)};\n"
        f"const expectedCount = {expected_count};\n"
        f"const initialRevision = {initial_revision};\n"
        f"process.stdout.write(String({expression}));\n"
    )
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, cwd=str(REPO_ROOT),
    )
    return completed.stdout.strip()



def _f6_build_outcome(*, wait_failed, capture_throws=False, snapshot_throws=False, final=None):
    """Drive the REAL shared outcome owner through node with injected faults.

    The live browser script and these cases call the same `F6_OUTCOME_OWNER_JS`, so a
    regression cannot pass while the script's control flow differs. Deterministic: no
    clock, no browser, no retry.
    """

    converged = {
        "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
        "revisions": [7] * 14, "metadataRequestCount": 2,
        "detailState": "changed", "detailText": "ok",
    }
    snapshot_body = (
        "() => { throw new Error('snapshot exploded'); }" if snapshot_throws
        else f"() => ({json.dumps(final if final is not None else converged)})"
    )
    capture_body = (
        "() => { throw new Error('capture exploded'); }" if capture_throws
        else "() => ({apply: {applied: true, reason: 'applied'}, deferred: {present: false}})"
    )
    wait_body = "new Error('Timed out after 20000ms')" if wait_failed else "null"
    script = F6_CONVERGENCE_PREDICATE_JS + F6_OUTCOME_OWNER_JS + (
        "\nconst outcome = f6BuildOutcome({\n"
        f"  waitFailure: {wait_body},\n"
        f"  captureDiagnostics: {capture_body},\n"
        f"  takeSnapshot: {snapshot_body},\n"
        "  observations: [], initialRevision: 1, expectedCount: 14,\n"
        "});\nprocess.stdout.write(JSON.stringify(outcome));\n"
    )
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, cwd=str(REPO_ROOT),
    )
    return json.loads(completed.stdout)


@pytest.mark.no_browser
def test_f6_outcome_owner_never_loses_or_mislabels_a_wait_failure():
    """A reporting fault must never erase the wait failure or imply the wait succeeded.

    The retired control flow put diagnostic capture and the final snapshot inside the
    same try as the success `done()`. A throw from either, AFTER the wait had already
    failed, fell into the success-side catch and emitted `postWaitError` with no `error`
    key at all - losing the real convergence failure and reading as a reporting-only
    fault on a successful wait.
    """

    # Failed wait, reporting healthy: the wait error stands alone.
    failed = _f6_build_outcome(wait_failed=True)
    assert failed["error"].startswith("Error: Timed out after 20000ms")
    assert failed["diagnosticCaptures"] == 1 and failed["diagnostics"] is not None
    assert "reportingError" not in failed and "postWaitError" not in failed

    # Failed wait + diagnostic capture throws: the ORIGINAL wait error survives, the
    # reporting fault is separate, and nothing claims success.
    capture_failed = _f6_build_outcome(wait_failed=True, capture_throws=True)
    assert capture_failed["error"].startswith("Error: Timed out after 20000ms")
    assert "capture exploded" in capture_failed["reportingError"]
    assert "postWaitError" not in capture_failed
    assert capture_failed["diagnosticCaptures"] == 1, "the attempt is still counted honestly"
    assert capture_failed["diagnostics"] is None, "nothing was safely captured"

    # Failed wait + final snapshot throws: same separation, and whatever WAS captured
    # safely is preserved.
    snapshot_failed = _f6_build_outcome(wait_failed=True, snapshot_throws=True)
    assert snapshot_failed["error"].startswith("Error: Timed out after 20000ms")
    assert "snapshot exploded" in snapshot_failed["reportingError"]
    assert "postWaitError" not in snapshot_failed
    assert snapshot_failed["diagnostics"] is not None, "a safe capture is not discarded"
    assert snapshot_failed["final"] is None and snapshot_failed["finalConverged"] is None

    # Successful wait + reporting throws: THIS is the only path that may say postWaitError.
    reporting_only = _f6_build_outcome(wait_failed=False, snapshot_throws=True)
    assert "error" not in reporting_only, "a successful wait must not invent a wait error"
    assert "snapshot exploded" in reporting_only["postWaitError"]
    assert "reportingError" not in reporting_only
    assert reporting_only["diagnosticCaptures"] == 0 and reporting_only["diagnostics"] is None

    # Successful wait, healthy reporting: the shared predicate reports the final state.
    healthy = _f6_build_outcome(wait_failed=False)
    assert "error" not in healthy and "postWaitError" not in healthy
    assert healthy["finalConverged"] is True and healthy["diagnosticCaptures"] == 0


@pytest.mark.no_browser
def test_f6_outcome_owner_reports_a_non_converged_final_state():
    """The final-state result comes from the shared predicate, not a Python restatement."""

    stalled = {
        "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
        "revisions": [7] * 13 + [1], "metadataRequestCount": 2,
        "detailState": "changed", "detailText": "ok",
    }
    outcome = _f6_build_outcome(wait_failed=True, final=stalled)
    assert outcome["finalConverged"] is False, "one stalled consumer is not convergence"
    assert outcome["error"].startswith("Error: Timed out after 20000ms")


@pytest.mark.no_browser
def test_f6_record_does_not_claim_the_retired_clause_for_a_current_failure():
    """A genuine current failure must not be labelled with the removed request-count clause.

    Shape taken from the real inline route: the request count never moves - which is
    legitimate - and one consumer is genuinely stalled. Under the retired classifier this
    would have been stamped `metadata_request_clause_unsatisfiable`, blaming a clause the
    live wait no longer contains and pointing a reader away from the real stall.
    """

    stalled_revisions = [7] * 13 + [1]
    record = json.loads(f6_wait_failure_record({
        "error": "Error: Timed out after 20000ms waiting for 14-session higher-revision consumer convergence",
        "initialRevision": 1,
        "diagnosticCaptures": 1,
        "diagnostics": {"apply": {"applied": True, "reason": "applied"}, "deferred": {"present": False}},
        "final": {
            "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
            "revisions": stalled_revisions, "metadataRequestCount": 2,
            "detailState": "changed", "detailText": "Live status is waiting for the chart snapshot",
        },
    }, metadata_requests_before=2))

    # No retired-clause verdict, under any key.
    assert "impossiblePredicate" not in record, record
    assert "metadata_request_clause_unsatisfiable" not in json.dumps(record), record
    # The real evidence is preserved: unchanged count AND the stalled consumer.
    assert record["metadataRequestsBefore"] == 2
    assert record["final"]["metadataRequestCount"] == 2
    assert record["final"]["revisions"] == stalled_revisions


# Escape-expanding adversarial characters. A control character costs six serialized
# characters and a quote or backslash costs two, so plain-ASCII padding never exercised
# the real failure: a 30-character budget can still serialize to 180 characters. Built
# with `chr()` so this source file stays plain ASCII and greppable.
F6_ESCAPE_EXPANDING = ("".join(chr(code) for code in (1, 2, 3, 4)) + '"' + "\\") * 700


@pytest.mark.no_browser
def test_f6_record_stays_compact_under_escape_expanding_strings():
    """Every runtime string full of escape-expanding characters must still fit the budget.

    Bounding by CHARACTER count is not the same as bounding the serialized record, and an
    ASCII-only regression could never tell the two apart. With every string built from
    control characters, quotes and backslashes, the smallest string budget still renders
    over 1,200 - so the record budget can only be held by surrendering low-value fields,
    and the record has to say which ones it gave up.
    """

    adversarial = F6_ESCAPE_EXPANDING
    record = f6_wait_failure_record({
        "error": f"Error: Timed out after 20000ms {adversarial}",
        "reportingError": f"TypeError: reporting exploded {adversarial}",
        "postWaitError": f"RangeError: post wait {adversarial}",
        "initialRevision": 1,
        "diagnosticCaptures": 1,
        "diagnostics": {
            "apply": {"applied": True, "reason": f"applied {adversarial}",
                      "session": f"session {adversarial}", "payloadGeneration": 7,
                      "appliedGeneration": 7},
            "deferred": {"present": None, "unreadable": f"ReferenceError {adversarial}"},
        },
        "final": {
            "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
            "revisions": [7] * 14, "metadataRequestCount": 2,
            "detailState": f"changed {adversarial}", "detailText": f"waiting {adversarial}",
        },
    }, 2)

    # The contract is hard: under budget, and still valid JSON.
    assert len(record) < F6_RECORD_BUDGET, f"record is {len(record)} chars and risks truncation"
    parsed = json.loads(record)

    # Character bounding alone genuinely does not get there - this proves the drop owner
    # is load-bearing here rather than dead code that happens to be exercised.
    assert len(json.dumps(
        _f6_record_at_budget({
            "error": f"Error {adversarial}", "reportingError": f"TypeError {adversarial}",
            "postWaitError": f"RangeError {adversarial}",
            "initialRevision": 1, "diagnosticCaptures": 1,
            "diagnostics": {"apply": {"reason": adversarial}, "deferred": {"unreadable": adversarial}},
            "final": {"detailState": adversarial, "detailText": adversarial},
        }, 2, F6_STRING_BUDGETS[-1]), sort_keys=True,
    )) >= F6_RECORD_BUDGET

    # Fixed-shape evidence survives intact - surrendering never touches a number or a
    # boolean, and the baseline that makes the request count interpretable stays.
    assert parsed["diagnosticCaptures"] == 1 and parsed["initialRevision"] == 1
    assert parsed["metadataRequestsBefore"] == 2
    assert parsed["apply"]["applied"] is True
    assert parsed["apply"]["payloadGeneration"] == 7 and parsed["apply"]["appliedGeneration"] == 7
    assert parsed["deferred"]["present"] is None
    assert parsed["final"]["revisions"] == [7] * 14
    assert parsed["final"]["sessionCount"] == 14 and parsed["final"]["metadataCount"] == 14
    assert parsed["final"]["statusRowCount"] == 14 and parsed["final"]["modelRowCount"] == 14
    assert parsed["final"]["metadataRequestCount"] == 2

    # BOTH identities survive: a failed wait and a reporting fault beside it. Neither is
    # ever on the drop list, because losing them would lose the failure itself.
    budget = parsed["stringBudget"]
    assert budget in F6_STRING_BUDGETS
    assert parsed["error"].startswith("Error: Timed out after 20000ms")
    assert parsed["reportingError"].startswith("TypeError: reporting exploded")

    # Every surviving bounded string reports its loss, with the original length retained.
    for scope, key in (
        (parsed, "error"), (parsed, "reportingError"),
        (parsed["deferred"], "unreadable"),
    ):
        assert len(scope[key]) <= budget, (key, len(scope[key]))
        assert scope[f"{key}Truncated"] is True, key
        assert scope[f"{key}Length"] > budget, key

    # Every surrendered field is named, and nothing is named that is still present.
    dropped = parsed["droppedFields"]
    assert dropped == ["final.detailText", "final.detailState", "apply.*"], dropped
    assert "detailText" not in parsed["final"] and "detailState" not in parsed["final"]
    assert not [key for key, value in parsed["apply"].items() if isinstance(value, str)]
    assert "unreadable" in parsed["deferred"]

    # The first line rule still holds: a stack trace cannot crowd out the projection.
    assert "\n" not in parsed["error"]


@pytest.mark.no_browser
def test_f6_record_raises_rather_than_returning_an_oversized_record():
    """The budget is a contract, not a preference: an unfittable core must raise.

    Returning an oversized record unmarked is the exact elision this helper exists to
    prevent, so when even the fixed-shape core cannot fit - here an enormous revisions
    array, which is never bounded because its length is the evidence - the helper refuses
    instead of handing back something a reader would take for complete.
    """

    with pytest.raises(AssertionError) as raised:
        f6_wait_failure_record({
            "error": "Error: Timed out",
            "initialRevision": 1,
            "diagnosticCaptures": 1,
            "diagnostics": {"apply": None, "deferred": None},
            "final": {"revisions": [123456789] * 500, "metadataRequestCount": 2},
        }, 2)

    message = str(raised.value)
    assert str(F6_RECORD_BUDGET) in message
    # The refusal must not smuggle the un-renderable payload into the message.
    assert "123456789, 123456789" not in message
    assert len(message) < F6_RECORD_BUDGET


@pytest.mark.no_browser
def test_f6_record_keeps_a_reporting_error_separate_from_the_wait_error():
    """A failed wait with a reporting fault must show BOTH, not one instead of the other."""

    record = json.loads(f6_wait_failure_record({
        "error": "Error: Timed out after 20000ms waiting for convergence\n    at waitFor",
        "reportingError": "TypeError: capture exploded\n    at captureF6FailureDiagnostics",
        "initialRevision": 1,
        "diagnosticCaptures": 1,
        "diagnostics": {"apply": None, "deferred": None},
        "final": {"metadataRequestCount": 2},
    }, 2))

    assert record["error"] == "Error: Timed out after 20000ms waiting for convergence"
    assert record["reportingError"] == "TypeError: capture exploded"
    # A reporting fault beside a failed wait is never reported as a post-wait fault.
    assert record["postWaitError"] == ""
    assert record["diagnosticCaptures"] == 1


@pytest.mark.no_browser
def test_f6_record_requires_the_baseline_argument():
    """The baseline is required; a silent default once let a call site drop it."""

    with pytest.raises(TypeError):
        f6_wait_failure_record({"error": "boom"})


@pytest.mark.no_browser
def test_f6_convergence_predicate_accepts_the_inline_payload_route():
    """Converged clients must pass even when no metadata request was ever issued.

    Measured twice on a frozen subject: `metadataRequestsBefore` and
    `metadataRequestCount` were both 2 while all fourteen sessions, metadata entries,
    status rows and model rows were present and every revision had advanced 1 -> 7.
    The retired clause required the count to INCREASE, which only the dataless
    `transcripts_changed` route does, so an inline payload made the wait unsatisfiable.
    """

    inline_converged = {
        "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
        "revisions": [7] * 14, "metadataRequestCount": 2,
    }

    # The corrected shared contract accepts it.
    assert _f6_predicate_says(
        "f6ConvergenceSatisfied(observation, expectedCount, initialRevision)", inline_converged,
    ) == "true"

    # The RETIRED contract - the same shared owner plus the transport clause, not a
    # copied predicate - rejects the very same converged state.
    assert _f6_predicate_says(
        "f6ConvergenceSatisfied(observation, expectedCount, initialRevision)"
        " && observation.metadataRequestCount > 2",
        inline_converged,
    ) == "false"


@pytest.mark.no_browser
def test_f6_convergence_predicate_still_rejects_incomplete_convergence():
    """Dropping the transport clause must not make the contract vacuous."""

    base = {
        "sessionCount": 14, "metadataCount": 14, "statusRowCount": 14, "modelRowCount": 14,
        "revisions": [7] * 14, "metadataRequestCount": 2,
    }
    call = "f6ConvergenceSatisfied(observation, expectedCount, initialRevision)"

    # Every convergence field is still load-bearing.
    for field in ("sessionCount", "metadataCount", "statusRowCount", "modelRowCount"):
        assert _f6_predicate_says(call, {**base, field: 13}) == "false", field

    # A single consumer left at the initial revision still fails.
    stalled = [7] * 14
    stalled[6] = 1
    assert _f6_predicate_says(call, {**base, "revisions": stalled}) == "false"

    # A revision BELOW the initial one fails too.
    regressed = [7] * 14
    regressed[0] = 0
    assert _f6_predicate_says(call, {**base, "revisions": regressed}) == "false"

    # A short or missing revisions list is not silently treated as converged.
    assert _f6_predicate_says(call, {**base, "revisions": [7] * 13}) == "false"
    assert _f6_predicate_says(call, {**base, "revisions": None}) == "false"


@pytest.mark.browser
def test_f6_failure_diagnostic_projection_is_reachable_and_serializable(
    browser, monkeypatch, gate_runtime_paths
):
    """The failure-only projection must work the moment F6 ever needs it.

    F6 evaluates `captureF6FailureDiagnostics` only after its wait has already
    failed, so a passing arm never exercises it. This probe drives the same
    shared snippet directly - no product transition is forced and no failure is
    manufactured - so an unreachable binding or an unserializable value is caught
    here instead of silently producing an empty diagnostic during a real red.
    """

    with _realistic_agent_browser_runtime(
        browser, monkeypatch, gate_runtime_paths, INITIAL_REALISTIC_SESSION_COUNT
    ) as fixture:
        captured = fixture.browser.execute_script(
            F6_FAILURE_DIAGNOSTIC_JS + "\nreturn captureF6FailureDiagnostics();"
        )

    # Reachable: both bindings resolved rather than raising, so neither key is absent.
    assert isinstance(captured, dict) and set(captured) == {"apply", "deferred"}, captured
    deferred = captured["deferred"]
    assert isinstance(deferred, dict) and "present" in deferred, captured
    # The deferred binding must be readable, not merely defaulted by an exception.
    assert "unreadable" not in deferred, captured
    assert deferred["present"] in (True, False), captured
    apply_projection = captured["apply"]
    if apply_projection is not None:
        assert set(apply_projection) == {
            "applied", "reason", "payloadGeneration", "appliedGeneration", "session",
        }, captured
        assert isinstance(apply_projection["reason"], str), captured
    # Serializable: WebDriver already round-tripped it as JSON, and it survives a
    # second explicit round trip with no non-JSON values hiding inside.
    assert json.loads(json.dumps(captured)) == captured, captured


@pytest.mark.browser
def test_f6_realistic_consumers_converge_to_the_published_roster_revision(
    browser, monkeypatch, gate_runtime_paths
):
    with _realistic_agent_browser_runtime(
        browser, monkeypatch, gate_runtime_paths, INITIAL_REALISTIC_SESSION_COUNT
    ) as fixture:
        initial = _wait_for_realistic_browser_roster(fixture.browser, fixture.runtime.sessions)
        initial_revision = max(initial["revisions"])
        initial_metadata_payload = fixture.runtime.app.build_transcripts_payload()
        assert len(initial_metadata_payload.get("sessions", {})) == INITIAL_REALISTIC_SESSION_COUNT
        real_build_transcripts_payload = fixture.runtime.app.build_transcripts_payload
        metadata_builds = 0

        def stale_then_current_metadata_payload():
            nonlocal metadata_builds
            metadata_builds += 1
            if metadata_builds == 1:
                return copy.deepcopy(initial_metadata_payload)
            return real_build_transcripts_payload()

        monkeypatch.setattr(
            fixture.runtime.app,
            "build_transcripts_payload",
            stale_then_current_metadata_payload,
        )
        # The status-roster transition must own convergence. A later cache-expiry request used to
        # rescue the standalone test by accident, while the loaded full gate remained on the first
        # 12-session build. Remove that unrelated fallback from this exact race: the authoritative
        # 14-session status revision must queue the second metadata build itself.
        monkeypatch.setattr(
            fixture.runtime.app,
            "start_metadata_refresh_for_request",
            lambda _requested_at, *, publish, defer=False: (False, 0),
        )
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
            // Retained for the diagnostic record only: the convergence contract above no
            // longer depends on it, because the count is a transport artifact.
            const metadataRequestsBefore = arguments[2];
            const done = arguments[arguments.length - 1];
            const observations = [];
            __F6_CONVERGENCE_PREDICATE__
            __F6_OUTCOME_OWNER__
            __F6_FAILURE_DIAGNOSTIC__
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
              // The wait owns its OWN try, so `waitFailure` is set by nothing else.
              // A fault while REPORTING a converged wait is not a convergence failure and
              // must never be dressed as one: an earlier revision let a post-wait
              // `snapshot()` or `done()` throw into the diagnostic catch and publish
              // wait-failure evidence for a wait that had actually succeeded.
              let waitFailure = null;
              try {
                await window.__yolomuxTestWaitFor(
                  () => f6ConvergenceSatisfied(snapshot(), expected.length, initialRevision),
                  {timeoutMs: 20000, description: `${expected.length}-session higher-revision consumer convergence`},
                );
              } catch (error) {
                waitFailure = error === undefined || error === null ? new Error('wait rejected') : error;
              }
              try {
                if (waitFailure === null) observations.push(snapshot());
                done(f6BuildOutcome({
                  waitFailure,
                  captureDiagnostics: captureF6FailureDiagnostics,
                  takeSnapshot: snapshot,
                  observations,
                  initialRevision,
                  expectedCount: expected.length,
                }));
              } finally {
                clearInterval(timer);
              }
            })();
            """
            .replace("__F6_CONVERGENCE_PREDICATE__", F6_CONVERGENCE_PREDICATE_JS)
            .replace("__F6_OUTCOME_OWNER__", F6_OUTCOME_OWNER_JS)
            .replace("__F6_FAILURE_DIAGNOSTIC__", F6_FAILURE_DIAGNOSTIC_JS),
            expected,
            initial_revision,
            metadata_requests_before,
        )
        # Report the wait outcome HERE, before scheduler/`advanced`/shape assertions can
        # fire first and bury the compact diagnostic under the full result repr.
        # A failed wait reports `error` and, if reporting also threw, `reportingError`
        # beside it - never instead of it. `postWaitError` means the wait SUCCEEDED and
        # only reporting failed, so it can never mask a convergence failure.
        assert not result.get("error"), f6_wait_failure_record(result, metadata_requests_before)
        assert not result.get("reportingError"), f6_wait_failure_record(result, metadata_requests_before)
        assert not result.get("postWaitError"), f6_wait_failure_record(result, metadata_requests_before)
        scheduler = fixture.runtime.app.client_events.snapshot()

    published = scheduler["published_by_type"].get("auto_approve_changed", {"events": 0, "bytes": 0})
    metadata_published = scheduler["published_by_type"].get("transcripts_changed", {"events": 0, "bytes": 0})
    assert published["events"] > 0 and published["bytes"] > 0, scheduler
    assert metadata_published["events"] > 0 and metadata_published["bytes"] > 0, scheduler
    # Immediate final-state validation through the SAME predicate the wait used, evaluated
    # in the browser on the final snapshot. A Python restatement here was weaker - it read
    # the observation history rather than the final state and never checked the revisions
    # array length - and could drift from the contract it was meant to re-prove.
    assert result.get("finalConverged") is True, f6_wait_failure_record(result, metadata_requests_before)
    # Reaching here means the wait converged: the diagnostic must never have run.
    assert result.get("diagnosticCaptures") == 0, f6_wait_failure_record(result, metadata_requests_before)
    assert result.get("diagnostics") is None, f6_wait_failure_record(result, metadata_requests_before)
    # Non-regression only. Requiring an INCREASE demanded one delivery mechanism and made
    # the wait unsatisfiable whenever the payload arrived inline; the count going BACKWARDS
    # would still be a real defect, so that invariant is kept.
    assert result["final"]["metadataRequestCount"] >= metadata_requests_before, (
        f6_wait_failure_record(result, metadata_requests_before)
    )
    assert set(added).issubset(expected), {"added": added, "expected": expected}

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Acceptance coverage for the reusable real-page browser harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from tests.browser_helpers.browser_console import assert_browser_journey_error_free
from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_console import acknowledge_browser_diagnostic_receipts
from tests.browser_helpers.browser_console import begin_browser_journey_surface_tracking
from tests.browser_helpers.browser_console import emit_js_debug_event
from tests.browser_helpers.browser_layout import WebDriverWait
from tests.gate_harness import wait_for_browser_boot
from tests.gate_harness import retire_expected_fixture_server_log_errors
from tests.tmux_runtime import run_isolated_tmux
from yolomux_lib.server_logs import SERVER_LOGS


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]


def _make_finder_repo(
    harness: Any,
    name: str,
    branch: str,
    children: tuple[str, ...],
) -> tuple[Path, Path]:
    repo = harness.runtime.paths.home_dir / "dev" / name
    for child in children:
        (repo / child).mkdir(parents=True)
    subprocess.run(
        ("git", "init", "-q", "-b", branch, str(repo)),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return repo, repo / children[0]


def _make_finder_repo_tree(harness: Any) -> tuple[Path, Path, Path]:
    repo, child = _make_finder_repo(
        harness,
        "ai-config",
        "master",
        ("assets", "backend", "frontend", "run", "tools"),
    )
    return repo.parent, repo, child


def _differ_payload(harness: Any, repo: Path) -> tuple[dict[str, object], Path]:
    target = repo / "changed.txt"
    target.write_text("fixture-driven Differ content\n", encoding="utf-8")
    stat = target.stat()
    session = str(harness.runtime.tmux.sessions[0])
    return {
        "session": session,
        "loaded": True,
        "errors": [],
        "refs_by_repo": {str(repo): {"from_ref": "HEAD", "to_ref": "current"}},
        "repos": [{"repo": str(repo), "count": 1}],
        "files": [{
            "session": session,
            "agent": "codex",
            "status": "M",
            "repo": str(repo),
            "path": target.name,
            "abs_path": str(target),
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "added": 1,
            "removed": 0,
        }],
    }, target


def test_real_page_harness_owns_7900s_runtime_and_user_action_helpers(e2e_browser: Any) -> None:
    dev, repo, child = _make_finder_repo_tree(e2e_browser)
    url = e2e_browser.load()

    assert url.startswith(f"http://127.0.0.1:{e2e_browser.runtime.port}/")
    assert 7900 <= e2e_browser.runtime.port <= 7999
    assert e2e_browser.runtime.paths.root in dev.parents

    e2e_browser.expand(dev, child_path=repo)

    def exercise_repo() -> dict[str, object]:
        e2e_browser.re_expand(repo, child_path=child)
        pending = e2e_browser.assert_no_pending_indicator(repo)
        return {"pending": pending, "child": e2e_browser.read_rendered_dom(e2e_browser.finder_row(child))}

    repeated = e2e_browser.assert_repeated(exercise_repo)
    assert len(repeated) == 5
    assert all(item["child"]["connected"] and "assets" in item["child"]["text"] for item in repeated)

    session = str(e2e_browser.runtime.tmux.sessions[0])
    panel = e2e_browser.switch_session(session)
    session_state = e2e_browser.assert_reaches_terminal_state(panel, bound=12)
    assert session_state["terminal"] is True


def test_pre_fix_real_page_content_assertion_and_spinner_assertion_are_independent(
    e2e_browser: Any,
) -> None:
    """Pin whether ef77f3fcb can render repo children while retaining pending UI."""

    dev, repo, child = _make_finder_repo_tree(e2e_browser)
    e2e_browser.load(tabs=("files",))
    e2e_browser.expand(dev, child_path=repo)
    e2e_browser.expand(repo, child_path=child)

    content = e2e_browser.read_rendered_dom(e2e_browser.finder_row(child))
    assert content["connected"] is True and "assets" in content["text"]
    e2e_browser.assert_no_pending_indicator(repo)


def test_pending_assertion_captures_evidence_after_content_is_already_present(e2e_browser: Any) -> None:
    """Prove the missing assertion catches the exact content-plus-spinner shape."""

    dev, repo, child = _make_finder_repo_tree(e2e_browser)
    e2e_browser.load(tabs=("files",))
    e2e_browser.expand(dev, child_path=repo)
    e2e_browser.expand(repo, child_path=child)
    repo_row = e2e_browser.finder_row(repo)
    content = e2e_browser.read_rendered_dom(e2e_browser.finder_row(child))
    assert content["connected"] is True and "assets" in content["text"]

    e2e_browser.driver.execute_script("arguments[0].classList.add('loading-children');", repo_row.element())
    try:
        with pytest.raises(AssertionError, match="retained a pending indicator") as failure:
            e2e_browser.assert_no_pending_indicator(repo_row)
    finally:
        e2e_browser.driver.execute_script("arguments[0].classList.remove('loading-children');", repo_row.element())

    evidence = e2e_browser.last_evidence
    assert evidence is not None and evidence.dom.is_file()
    assert evidence.screenshot is not None and evidence.screenshot.is_file()
    assert str(evidence.dom) in str(failure.value)


def test_direct_internal_differ_fixture_path_reaches_terminal_state(e2e_browser: Any) -> None:
    """Positive control: direct payload injection bypasses the broken real-page wiring."""

    _dev, repo, _child = _make_finder_repo_tree(e2e_browser)
    payload, target = _differ_payload(e2e_browser, repo)
    e2e_browser.load()
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={
            "applySessionFilesPayloadFromPush": "function",
            "clientEventDemandDescriptor": "function",
            "clientSessionFilesWatchRequests": "function",
            "openFileSurface": "function",
            "renderFileExplorerChangesPanels": "function",
        },
        dom_anchors=("#grid",),
        timeout=12,
    )
    metrics = e2e_browser.driver.execute_async_script(
        """
        const payload = arguments[0];
        const path = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileSurface(differItemId);
            const request = await window.__yolomuxTestWaitFor(
              () => clientSessionFilesWatchRequests()[0] || null,
              {timeoutMs: 4000, description: 'fixture-driven Differ watch request'},
            );
            const applied = applySessionFilesPayloadFromPush(payload, request) === true;
            renderFileExplorerChangesPanels({force: true, view: 'differ'});
            const row = await window.__yolomuxTestWaitFor(
              () => document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`),
              {timeoutMs: 4000, description: `fixture-driven Differ row for ${path}`},
            );
            done({applied, rowConnected: row?.isConnected === true});
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        payload,
        str(target),
    )
    assert not metrics.get("error") and metrics == {"applied": True, "rowConnected": True}, metrics
    state = e2e_browser.assert_reaches_terminal_state("#panel-__differ__", bound=4)
    assert state["terminal"] is True and state["pending"] == [], state


def test_real_differ_click_reaches_content_or_typed_error_within_bound(e2e_browser: Any) -> None:
    _make_finder_repo_tree(e2e_browser)
    e2e_browser.load()
    differ = e2e_browser.open_differ()
    state = e2e_browser.assert_reaches_terminal_state(differ, bound=12)
    assert state["terminal"] is True


def test_authenticated_real_page_finder_repeats_without_pending(authenticated_e2e_browser: Any) -> None:
    """A form-authenticated browser must clear pending state on the two reported repo rows."""

    ai_config, ai_child = _make_finder_repo(
        authenticated_e2e_browser,
        "ai-config",
        "master",
        ("assets", "backend", "frontend", "run", "tools"),
    )
    ant, ant_child = _make_finder_repo(
        authenticated_e2e_browser,
        "ant",
        "main",
        ("assets", "backend", "frontend", "run", "tools"),
    )
    authentication = authenticated_e2e_browser.authentication
    assert authentication is not None
    assert authentication.username == "e2e-admin" and authentication.role == "admin"
    assert any(name.startswith("yolomux_auth_") for name in authentication.cookie_names)

    session = str(authenticated_e2e_browser.runtime.tmux.sessions[0])
    respawned = run_isolated_tmux(
        authenticated_e2e_browser.runtime.tmux,
        "respawn-pane",
        "-k",
        "-t",
        f"{session}:",
        "-c",
        str(ai_config),
        "bash",
    )
    assert respawned.returncode == 0, respawned.stderr or respawned.stdout

    def expected_pane_cwd(_driver: object) -> subprocess.CompletedProcess[str] | bool:
        result = run_isolated_tmux(
            authenticated_e2e_browser.runtime.tmux,
            "display-message",
            "-p",
            "-t",
            f"{session}:",
            "#{pane_current_path}",
        )
        return result if result.returncode == 0 and result.stdout.strip() == str(ai_config) else False

    pane_cwd = WebDriverWait(authenticated_e2e_browser.driver, 12).until(expected_pane_cwd)
    assert pane_cwd.returncode == 0 and pane_cwd.stdout.strip() == str(ai_config), pane_cwd.stderr or pane_cwd.stdout
    WebDriverWait(authenticated_e2e_browser.driver, 12).until(
        lambda _driver: (
            authenticated_e2e_browser.runtime.app.activity_transcript_service.transcripts_payload_cache_record.worker
            is None
        )
    )
    authenticated_e2e_browser.runtime.app.set_transcripts_payload_cache(
        authenticated_e2e_browser.runtime.app.build_transcripts_payload()
    )

    authenticated_e2e_browser.load(tabs=("files", session))
    metadata = authenticated_e2e_browser.driver.execute_async_script(
        """
        const session = arguments[0];
        const expectedRoot = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const response = await fetch('/api/session-metadata?force=1', {cache: 'no-store'});
            const raw = await response.json();
            const rendered = await window.__yolomuxTestWaitFor(() => {
              const info = transcriptMetadataState.payload.sessions?.[session];
              const summary = sessionWorkSummary(session, info);
              const tab = document.querySelector(`[data-pane-tab="${CSS.escape(session)}"]`);
              const popover = typeof paneTabPopoverForAnchor === 'function'
                ? paneTabPopoverForAnchor(tab)
                : tab?.querySelector?.(':scope > .session-popover');
              const popoverText = String(popover?.textContent || '');
              if (summary.git?.root !== expectedRoot || summary.git?.branch !== 'master') return null;
              if (!tab || !popover || !popoverText.includes('master') || !popoverText.includes(expectedRoot)) return null;
              return {
                grid: document.querySelector('#grid') !== null,
                tabText: String(tab.textContent || ''),
                tabAriaLabel: String(tab.getAttribute('aria-label') || ''),
                popoverText,
                graphRoot: summary.git.root,
                graphBranch: summary.git.branch,
              };
            }, {timeoutMs: 12000, description: 'authenticated metadata-driven tab and popover'});
            done({
              status: response.status,
              state: raw?.state || '',
              requestId: raw?.request?.id || '',
              hasCanonicalSession: Boolean(raw?.data?.sessions?.[session]),
              hasFlattenedSessions: Object.prototype.hasOwnProperty.call(raw || {}, 'sessions'),
              rendered,
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        session,
        str(ai_config),
    )
    assert not metadata.get("error"), metadata
    assert metadata["status"] == 200 and metadata["state"] == "ready" and metadata["requestId"], metadata
    assert metadata["hasCanonicalSession"] is True and metadata["hasFlattenedSessions"] is False, metadata
    assert metadata["rendered"]["grid"] is True, metadata
    assert metadata["rendered"]["graphRoot"] == str(ai_config) and metadata["rendered"]["graphBranch"] == "master", metadata
    assert "master" in metadata["rendered"]["tabAriaLabel"] and str(ai_config) in metadata["rendered"]["popoverText"], metadata

    authenticated_e2e_browser.expand(ai_config.parent, child_path=ai_config)
    for repo, child, branch in ((ai_config, ai_child, "master"), (ant, ant_child, "main")):
        label = authenticated_e2e_browser.driver.execute_async_script(
            """
            const row = arguments[0];
            const branch = arguments[1];
            const done = arguments[arguments.length - 1];
            window.__yolomuxTestWaitFor(
              () => String(row.innerText || '').includes(`[${branch}]`) ? String(row.innerText || '') : null,
              {timeoutMs: 12000, description: `authenticated Finder branch badge ${branch}`},
            ).then(done, error => done({error: String(error?.stack || error)}));
            """,
            authenticated_e2e_browser.finder_row(repo).element(),
            branch,
        )
        assert not isinstance(label, dict) and f"[{branch}]" in label, label

        def exercise_repo() -> dict[str, object]:
            authenticated_e2e_browser.re_expand(repo, child_path=child)
            pending = authenticated_e2e_browser.assert_no_pending_indicator(repo)
            child_dom = authenticated_e2e_browser.read_rendered_dom(authenticated_e2e_browser.finder_row(child))
            return {"pending": pending, "child": child_dom}

        repeated = authenticated_e2e_browser.assert_repeated(exercise_repo, times=5)
        assert len(repeated) == 5
        assert all(item["child"]["connected"] is True for item in repeated)

    authenticated_e2e_browser.driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
        authenticated_e2e_browser.finder_row(ant).element(),
    )
    evidence = authenticated_e2e_browser.capture_failure("authenticated-finder-pass")
    assert evidence.screenshot is not None and evidence.screenshot.is_file()
    assert evidence.dom.is_file()


def test_authenticated_release_soak_gates_browser_and_server_failures_without_stats_panel(
    authenticated_e2e_browser: Any,
) -> None:
    """The release soak fails from retained browser and server evidence without opening YO!stats."""

    authentication = authenticated_e2e_browser.authentication
    assert authentication is not None and authentication.role == "admin"
    driver = authenticated_e2e_browser.driver
    begin_browser_journey_surface_tracking(driver)
    driver.get_log("browser")
    baseline = driver.execute_script(
        """
        window.__releaseGateLogsRequests = 0;
        window.__releaseGateOriginalFetch = window.fetch;
        window.fetch = (input, options = {}) => {
          const path = new URL(String(input), location.href).pathname;
          if (path === '/api/logs') window.__releaseGateLogsRequests += 1;
          return window.__releaseGateOriginalFetch(input, options);
        };
        return {
          statsPanelPresent: document.querySelector('.js-debug-panel') !== null,
          logsRequests: window.__releaseGateLogsRequests,
        };
        """
    )
    assert baseline == {"statsPanelPresent": False, "logsRequests": 0}

    acknowledge_browser_diagnostic_receipts(driver)
    clean = assert_browser_journey_error_free(driver)
    assert clean["browserLocalFailures"] == []
    assert clean["serverLogErrors"] == []
    clean_ring_reads = driver.execute_script("return window.__releaseGateLogsRequests;")
    assert clean_ring_reads > 0

    stats_failure_event = emit_js_debug_event(
        driver,
        "stats_history",
        {
            "level": "warning",
            "message": "production graph refresh failed",
            "wallTime": "2026-08-05 13:23:45 PDT",
            "requestId": "r-production-graph-17",
            "source": "stats-current",
            "endpoint": "/api/stats-snapshot",
            "eventType": "graph-refresh",
            "deliveryOutcome": "failed",
        },
    )
    with pytest.raises(AssertionError, match="production graph refresh failed") as failure:
        assert_browser_journey_error_free(driver)
    evidence = str(failure.value)
    evidence_payload = json.loads(evidence.removeprefix("browser journey emitted errors: "))
    for retained in (
        '"requestId": "r-production-graph-17"',
        '"source": "stats-current"',
        '"route": "/api/stats-snapshot"',
        '"event": "graph-refresh"',
        '"deliveryOutcome": "failed"',
    ):
        assert retained in evidence, evidence
    assert evidence_payload["browserLocalFailures"][0]["wallTime"] == "2026-08-05 13:23:45 PDT"
    assert evidence_payload["browserLocalFailures"][0]["wallTime"] != stats_failure_event["ts"]
    assert driver.execute_script("return window.__releaseGateLogsRequests;") > clean_ring_reads
    assert driver.execute_script("return document.querySelector('.js-debug-panel') === null;") is True
    assert acknowledge_and_consume_only_expected_js_debug_failures(driver, (stats_failure_event,)) == (
        stats_failure_event,
    )

    client_failure_event = emit_js_debug_event(
        driver,
        "client_failure",
        {
            "level": "error",
            "message": "authenticated activity graph refresh failed",
            "wallTime": "2026-08-05 13:24:00 PDT",
            "requestId": "r-authenticated-activity-graph-18",
            "source": "activity-graph",
            "endpoint": "/api/activity-summary",
            "eventType": "graph-refresh",
            "deliveryOutcome": "failed",
        },
    )
    with pytest.raises(AssertionError, match="authenticated activity graph refresh failed") as error_failure:
        assert_browser_journey_error_free(driver)
    error_evidence = json.loads(
        str(error_failure.value).removeprefix("browser journey emitted errors: ")
    )
    assert len(error_evidence["browserLocalFailures"]) == 1, error_evidence
    error_record = error_evidence["browserLocalFailures"][0]
    assert {
        "level": error_record["level"],
        "message": error_record["message"],
        "requestId": error_record["requestId"],
        "source": error_record["source"],
        "route": error_record["route"],
        "event": error_record["event"],
        "wallTime": error_record["wallTime"],
        "deliveryOutcome": error_record["deliveryOutcome"],
    } == {
        "level": "error",
        "message": "authenticated activity graph refresh failed",
        "requestId": "r-authenticated-activity-graph-18",
        "source": "activity-graph",
        "route": "/api/activity-summary",
        "event": "graph-refresh",
        "wallTime": "2026-08-05 13:24:00 PDT",
        "deliveryOutcome": "failed",
    }
    assert error_record["wallTime"] != client_failure_event["ts"]
    assert driver.execute_script("return document.querySelector('.js-debug-panel') === null;") is True
    assert acknowledge_and_consume_only_expected_js_debug_failures(driver, (client_failure_event,)) == (
        client_failure_event,
    )
    server_warning = {
        "level": "warning",
        "source": "local-service:statusd",
        "category": "transport",
        "message": "authenticated server-only transport warning",
    }
    SERVER_LOGS.emit(
        server_warning["level"],
        server_warning["source"],
        server_warning["message"],
        category=server_warning["category"],
    )
    with pytest.raises(AssertionError, match=server_warning["message"]) as server_failure:
        assert_browser_journey_error_free(driver)
    server_evidence = str(server_failure.value)
    assert '"source": "local-service:statusd"' in server_evidence
    assert '"level": "warning"' in server_evidence
    assert retire_expected_fixture_server_log_errors(
        driver,
        authenticated_e2e_browser.runtime,
        (server_warning,),
    )[0]["message"] == server_warning["message"]
    assert driver.execute_script("return document.querySelector('.js-debug-panel') === null;") is True

    clean_after_failure = assert_browser_journey_error_free(driver)
    assert clean_after_failure["browserLocalFailures"] == [] and clean_after_failure["serverLogErrors"] == []
    driver.execute_script(
        "window.fetch = window.__releaseGateOriginalFetch; delete window.__releaseGateOriginalFetch;"
    )
